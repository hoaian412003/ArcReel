"""起始帧联合图：提示词与参考图的装配，以及生成后按格切分。

提示词由 ``reference_video/keyframe_sheet`` 模版渲染，本模块产出槽位值与随请求发出的参考图顺序。
参考图顺序即「Image N」的编号：先场景资产图，再按出镜角色首现顺序逐个给（有脸图的才给）「脸部特写」与「全身三视图」。
参考图里不放任何视频帧：视频帧会把那一单元的偏差带进来（见 ``docs/adr/0096``）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from lib.keyframes.models import GRID_COLUMNS, GRID_ROWS, KeyframeSheet
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script.grid.splitter import is_placeholder_cell, split_grid_image

TEMPLATE_ID = "reference_video/keyframe_sheet"


class KeyframeSheetError(ValueError):
    """装配联合图所需的素材缺失或不可用；``code`` 是机读原因，``name`` 是出问题的资产。"""

    def __init__(self, code: str, name: str) -> None:
        self.code = code
        self.name = name
        super().__init__(f"{code}: {name}")


@dataclass(frozen=True)
class SheetPrompt:
    prompt: str
    #: 与「图N」编号严格同序的参考图路径。
    reference_images: tuple[Path, ...]
    aspect_ratio: str
    #: 与 ``reference_images`` 逐项对应的说明（「图N为…的参考」），供界面标注每张参考图。
    reference_labels: tuple[str, ...] = ()


def _character_entry(project: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """``(承载形象的条目, 描述所在条目)``：本体取自身；``本体/衍生`` 的形象取衍生，描述并入本体。"""
    base, _, derivative = name.partition("/")
    characters = project.get("characters")
    entry = characters.get(base) if isinstance(characters, Mapping) else None
    if not isinstance(entry, Mapping):
        raise KeyframeSheetError("keyframe_character_missing", name)
    if not derivative:
        return entry, entry
    derivatives = entry.get("derivatives")
    sub = derivatives.get(derivative) if isinstance(derivatives, Mapping) else None
    if not isinstance(sub, Mapping):
        raise KeyframeSheetError("keyframe_character_missing", name)
    return sub, sub


def character_sheet_path(project: Mapping[str, Any], project_path: Path, name: str) -> Path:
    """出镜角色的全身三视图路径；登记缺失或文件不在抛 :class:`KeyframeSheetError`。"""
    entry, _ = _character_entry(project, name)
    relative = entry.get("character_sheet")
    path = project_path / relative if isinstance(relative, str) and relative else None
    if path is None or not path.is_file():
        raise KeyframeSheetError("keyframe_character_sheet_missing", name)
    return path


#: 角色卡上「脸部特写」登记为本体的一条衍生，名字固定（与前端 ``FACE_DERIVATIVE_NAME`` 一致）。
FACE_DERIVATIVE_NAME = "Face"


def face_reference_path(project: Mapping[str, Any], project_path: Path, name: str) -> Path | None:
    """出镜角色已登记并已生成的脸部特写路径；没有则返回 None，由全身图承担脸部参考。

    只有本体有脸图：``本体/衍生``（如年轻形态）的长相随本体脸图而来、但年龄不同，
    套用本体脸图会把它拉回本体的年龄，所以衍生只用自己的全身图。
    """
    if "/" in name:
        return None
    entry, _ = _character_entry(project, name)
    derivatives = entry.get("derivatives")
    face = derivatives.get(FACE_DERIVATIVE_NAME) if isinstance(derivatives, Mapping) else None
    relative = face.get("character_sheet") if isinstance(face, Mapping) else None
    path = project_path / relative if isinstance(relative, str) and relative else None
    return path if path is not None and path.is_file() else None


def scene_sheet_path(project: Mapping[str, Any], project_path: Path, scene: str) -> Path:
    scenes = project.get("scenes")
    entry = scenes.get(scene) if isinstance(scenes, Mapping) else None
    relative = entry.get("scene_sheet") if isinstance(entry, Mapping) else None
    path = project_path / relative if isinstance(relative, str) and relative else None
    if path is None or not path.is_file():
        raise KeyframeSheetError("keyframe_scene_sheet_missing", scene)
    return path


def _cell_position(index: int) -> dict[str, int]:
    return {"number": index + 1, "row": index // GRID_COLUMNS + 1, "col": index % GRID_COLUMNS + 1}


def _people_entry(character: Mapping[str, Any]) -> str:
    if character["face"] is None:
        return f"{character['name']} (face, build and outfit: Image {character['body']})"
    return f"{character['name']} (face: Image {character['face']}, build and outfit: Image {character['body']})"


def build_sheet_prompt(
    sheet: KeyframeSheet,
    *,
    project: Mapping[str, Any],
    project_path: Path,
    extra_references: Sequence[tuple[Path, str]] = (),
) -> SheetPrompt:
    """装配一张联合图的提示词与参考图。素材缺失抛 :class:`KeyframeSheetError`。

    ``extra_references`` 是用户在画布上额外接到这张联合图的参考图（路径，说明），排在规划引用的图之后；
    与已有参考图同一文件的跳过。
    """
    aspect_ratio = str(project.get("aspect_ratio") or "16:9")
    references: list[Path] = [scene_sheet_path(project, project_path, sheet.scene)]
    declarations = [
        f"Image 1: the scene “{sheet.scene}”. Use it only for the setting, architecture and lighting. "
        "Do not copy any person or text from it."
    ]

    names: list[str] = []
    for cell in sheet.cells:
        for name in cell.characters:
            if name not in names:
                names.append(name)

    characters: list[dict[str, Any]] = []
    for name in names:
        face = face_reference_path(project, project_path, name)
        full = character_sheet_path(project, project_path, name)
        face_number: int | None = None
        if face is not None:
            references.append(face)
            face_number = len(references)
            declarations.append(
                f"Image {face_number}: close-up of {name}'s face. The identity reference for this character's face, "
                "skin tone and hair."
            )
        references.append(full)
        body_number = len(references)
        declarations.append(
            f"Image {body_number}: full-body turnaround of {name}. "
            + (
                "The reference for build, proportions and outfit."
                if face is not None
                else "The reference for face, skin tone, hair, build, proportions and outfit."
            )
        )
        appearance = "" if "/" in name else str(_character_entry(project, name)[1].get("description") or "").strip()
        characters.append({"name": name, "face": face_number, "body": body_number, "appearance": appearance})
    by_name = {item["name"]: item for item in characters}

    for path, label in extra_references:
        if path in references:
            continue
        references.append(path)
        declarations.append(
            f"Image {len(references)}: {label or 'extra reference'}. "
            "An extra reference chosen by the user; use it for the identity and look of the character it shows."
        )

    total = GRID_COLUMNS * GRID_ROWS
    prompt = builtin_templates.render(
        TEMPLATE_ID,
        reference_images="\n".join(declarations),
        rows=GRID_ROWS,
        cols=GRID_COLUMNS,
        cell_count=total,
        grid_aspect_ratio=aspect_ratio,
        panel_aspect_ratio=aspect_ratio,
        scene_name=sheet.scene,
        characters=characters,
        cells=[
            {
                **_cell_position(index),
                "people": "; ".join(_people_entry(by_name[name]) for name in cell.characters),
                "staging": cell.staging,
                "framing": cell.framing,
            }
            for index, cell in enumerate(sheet.cells)
        ],
        placeholders=[_cell_position(index) for index in range(len(sheet.cells), total)],
        style=normalize_style_value(project.get("style")),
        style_description=normalize_style_value(project.get("style_description")),
    )
    return SheetPrompt(
        prompt=prompt,
        reference_images=tuple(references),
        aspect_ratio=aspect_ratio,
        reference_labels=tuple(declarations),
    )


def split_sheet(sheet_image: Path, cell_count: int, aspect_ratio: str) -> list[Image.Image]:
    """把联合图按 2×2 切开，取前 ``cell_count`` 格。

    被取的格子若是纯灰占位（模型没有画出内容）抛 :class:`KeyframeSheetError`：切到占位当成起始帧
    会把一张灰图发给视频模型。
    """
    with Image.open(sheet_image) as source:
        cells = split_grid_image(source.convert("RGB"), GRID_ROWS, GRID_COLUMNS, aspect_ratio)
    taken = cells[:cell_count]
    empty = [index + 1 for index, cell in enumerate(taken) if is_placeholder_cell(cell)]
    if empty:
        raise KeyframeSheetError("keyframe_cell_empty", ",".join(str(i) for i in empty))
    return taken
