"""起始帧规划：把一次文本调用产出的格子清单机械校验并装进联合图。

规划的创作判断（哪些单元共用一格、站位怎么写）由文本模型给出；这里只做确定性的事：
清单与单元、角色、场景的对应校验，以及按场景把格子装进每张至多 4 格的联合图。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from lib.keyframes.models import CELLS_PER_SHEET, KeyframeBoard, KeyframeCell, KeyframeSheet, digest
from lib.script.reference_video.text_parser import extract_mentions

_STRICT = ConfigDict(extra="forbid")


class PlannedCell(BaseModel):
    """文本模型输出的一格；编号与状态由 :func:`build_board` 赋予。"""

    model_config = _STRICT

    label: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    staging: str = Field(min_length=1)
    framing: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    unit_ids: list[str] = Field(min_length=1)


class KeyframePlanViolation(ValueError):
    """规划违约：``problems`` 逐条列出，调用方整体拒绝、不落盘。"""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


@dataclass(frozen=True)
class PlanResult:
    board: KeyframeBoard
    #: 没有被任何格子覆盖的单元：它们按现有路径生成，不带起始帧。
    uncovered_unit_ids: list[str]


def plan_fingerprint(units: Sequence[Mapping[str, object]]) -> str:
    """规划依据：各单元的编号、正文与导演说明。其中任一变化，既有规划即视为过期。"""
    return digest([[str(u.get("unit_id")), str(u.get("text") or ""), str(u.get("direction") or "")] for u in units])


def _mentioned_names(text: str) -> set[str]:
    """单元正文画面描述里 ``@`` 引用的名字（说话人位不计）；衍生保留 ``本体/衍生`` 全名。"""
    return set(extract_mentions(text))


def build_board(
    episode: int,
    planned: Sequence[PlannedCell],
    *,
    units: Sequence[Mapping[str, object]],
    characters: Sequence[str],
    scenes: Sequence[str],
) -> PlanResult:
    """校验清单并装配看板；任何违约抛 :class:`KeyframePlanViolation`。"""
    unit_text = {str(u["unit_id"]): str(u.get("text") or "") for u in units if u.get("unit_id")}
    registered_characters = set(characters)
    registered_scenes = set(scenes)
    problems: list[str] = []
    owner: dict[str, int] = {}

    for index, cell in enumerate(planned, start=1):
        name = f"第 {index} 格「{cell.label}」"
        if cell.scene not in registered_scenes:
            problems.append(f"{name}的场景「{cell.scene}」没有登记；只能用已登记的场景资产")
        unknown = [c for c in cell.characters if c not in registered_characters]
        if unknown:
            problems.append(f"{name}的出镜角色{unknown}没有登记")
        for unit_id in cell.unit_ids:
            if unit_id not in unit_text:
                problems.append(f"{name}引用了不存在的单元 {unit_id}")
                continue
            if unit_id in owner:
                problems.append(f"单元 {unit_id} 同时属于第 {owner[unit_id]} 格与第 {index} 格；一个单元只用一格")
            owner[unit_id] = index
            missing = [
                c
                for c in cell.characters
                if c in registered_characters and c not in _mentioned_names(unit_text[unit_id])
            ]
            if missing:
                problems.append(
                    f"{name}写了出镜角色{missing}，但单元 {unit_id} 的正文画面描述没有 @ 引用他们；"
                    "没有参考图的角色不该出现在这个单元的起始帧里"
                )
    if problems:
        raise KeyframePlanViolation(problems)

    ordered_scenes: list[str] = []
    for cell in planned:
        if cell.scene not in ordered_scenes:
            ordered_scenes.append(cell.scene)

    sheets: list[KeyframeSheet] = []
    number = 0
    for scene in ordered_scenes:
        scene_cells = [cell for cell in planned if cell.scene == scene]
        for start in range(0, len(scene_cells), CELLS_PER_SHEET):
            chunk = scene_cells[start : start + CELLS_PER_SHEET]
            cells = []
            for cell in chunk:
                number += 1
                cells.append(KeyframeCell(id=f"K{number:02d}", **cell.model_dump()))
            sheets.append(KeyframeSheet(id=f"S{len(sheets) + 1}", scene=scene, cells=cells))

    board = KeyframeBoard(episode=episode, plan_fingerprint=plan_fingerprint(units), sheets=sheets)
    covered = set(owner)
    uncovered = [uid for uid in unit_text if uid not in covered]
    return PlanResult(board=board, uncovered_unit_ids=uncovered)
