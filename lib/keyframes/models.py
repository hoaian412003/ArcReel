"""起始帧看板的数据模型：联合图 → 格子 → 使用它的视频单元。

看板整份落在 ``drafts/episode_N/keyframes.json``，不进 ``project.json`` 与正式脚本。单元与格子的映射
只存在这里：单元正文与 ``h3_prompt`` 载荷不因看板变化而过期。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 一张联合图的格子数上限（2×2）。
CELLS_PER_SHEET = 4
#: 联合图的网格列数与行数；不足 4 格的位置留灰色占位，切分时丢弃。
GRID_COLUMNS = 2
GRID_ROWS = 2

CellStatus = Literal["pending", "draft", "approved"]

_STRICT = ConfigDict(extra="forbid")


class KeyframeCell(BaseModel):
    """一格：某站位下某起始景别的画面，供一个或多个视频单元作首帧参考。"""

    model_config = _STRICT

    id: str = Field(pattern=r"^K\d{2,}$", description="格子编号，整集唯一，如 K01")
    label: str = Field(min_length=1, description="审阅界面显示的简称")
    scene: str = Field(min_length=1, description="发生地的场景资产名")
    staging: str = Field(min_length=1, description="站位：谁在哪一侧、坐或站、朝向、手中物件、服装")
    framing: str = Field(min_length=1, description="这一格的起始景别：景别、机位角度、画面主体")
    characters: list[str] = Field(default_factory=list, description="出镜角色的登记名")
    unit_ids: list[str] = Field(min_length=1, description="以这一格作首帧的视频单元")
    status: CellStatus = "pending"
    image: str | None = Field(default=None, description="切出的格子图，项目内相对路径")
    basis: str | None = Field(default=None, description="生成这一格时依据的指纹；与当前依据不同即过期")

    @model_validator(mode="after")
    def _check(self) -> KeyframeCell:
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError(f"格子 {self.id} 的 unit_ids 有重复")
        if len(set(self.characters)) != len(self.characters):
            raise ValueError(f"格子 {self.id} 的 characters 有重复")
        if self.status != "pending" and not self.image:
            raise ValueError(f"格子 {self.id} 状态为 {self.status} 但没有图")
        return self


class KeyframeSheet(BaseModel):
    """一张联合图：同一场景的至多 4 格，按行优先排布。"""

    model_config = _STRICT

    id: str = Field(pattern=r"^S\d+$", description="联合图编号，整集唯一，如 S1")
    scene: str = Field(min_length=1)
    cells: list[KeyframeCell] = Field(min_length=1, max_length=CELLS_PER_SHEET)
    image: str | None = Field(default=None, description="联合图整图，项目内相对路径")

    @model_validator(mode="after")
    def _check(self) -> KeyframeSheet:
        wrong = [cell.id for cell in self.cells if cell.scene != self.scene]
        if wrong:
            raise ValueError(f"联合图 {self.id} 的格子 {wrong} 不属于场景 {self.scene}")
        return self


class KeyframeBoard(BaseModel):
    """一集的起始帧看板。"""

    model_config = _STRICT

    version: int = 1
    episode: int = Field(ge=1)
    plan_fingerprint: str = Field(description="规划时所依据的单元内容摘要；变化即规划过期")
    sheets: list[KeyframeSheet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> KeyframeBoard:
        cell_ids = [cell.id for sheet in self.sheets for cell in sheet.cells]
        if len(set(cell_ids)) != len(cell_ids):
            raise ValueError("格子编号在整集内必须唯一")
        if len({sheet.id for sheet in self.sheets}) != len(self.sheets):
            raise ValueError("联合图编号在整集内必须唯一")
        owners: dict[str, str] = {}
        for sheet in self.sheets:
            for cell in sheet.cells:
                for unit_id in cell.unit_ids:
                    if unit_id in owners:
                        raise ValueError(f"单元 {unit_id} 同时属于格子 {owners[unit_id]} 与 {cell.id}")
                    owners[unit_id] = cell.id
        return self

    def cells(self) -> list[KeyframeCell]:
        return [cell for sheet in self.sheets for cell in sheet.cells]

    def cell_for_unit(self, unit_id: str) -> KeyframeCell | None:
        for cell in self.cells():
            if unit_id in cell.unit_ids:
                return cell
        return None

    def sheet_of(self, cell_id: str) -> KeyframeSheet | None:
        for sheet in self.sheets:
            if any(cell.id == cell_id for cell in sheet.cells):
                return sheet
        return None


def digest(*parts: object) -> str:
    """稳定摘要：各部分按 JSON 规范化后取 SHA-256 前 16 位。"""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
