"""Flow 看板：把每一集的制作状态映射到「Agent 单元 / 人工单元」交替的列。

列按制作顺序排列，``agent`` 列由 Agent 推进，``human`` 列等人工决定。映射是对单集
``WorkflowStatus`` 的纯函数：状态名决定列，状态上的阻断只标 ``blocked``，不改列。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from lib.workflow.workflow_state import WorkflowStateName, WorkflowStatus

ColumnKind = Literal["agent", "human"]

#: 看板列，按制作顺序。
FLOW_COLUMNS: tuple[tuple[str, ColumnKind], ...] = (
    ("script_plan", "agent"),
    ("script_plan_review", "human"),
    ("direction", "agent"),
    ("direction_review", "human"),
    ("prompts", "agent"),
    ("assets", "agent"),
    ("video", "agent"),
    ("done", "agent"),
)

_COLUMN_BY_STATE: dict[WorkflowStateName, str] = {
    "PROJECT_INPUT": "script_plan",
    "SELLING_POINTS": "script_plan",
    "ASSET_INVENTORY": "script_plan",
    "EPISODE_PLAN": "script_plan",
    "SCREENPLAY": "script_plan",
    "SCREENPLAY_REVIEW": "script_plan_review",
    "SCRIPT_PLAN_CONTENT": "script_plan",
    "SCRIPT_PLAN_REVIEW": "script_plan_review",
    "DIRECTOR_PLAN": "direction",
    "DIRECTOR_REVIEW": "direction_review",
    "FINAL_SCRIPT": "prompts",
    "ASSET_SHEETS": "assets",
    "STORYBOARD": "assets",
    "VIDEO": "video",
    "EXPORT_READY": "done",
}


class FlowColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ColumnKind


class FlowCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int
    title: str
    column: str
    next_action: str
    blocked: bool


class FlowBoard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[FlowColumn]
    cards: list[FlowCard]


def column_for_state(state: WorkflowStateName) -> str:
    """单集状态落在哪一列。"""
    return _COLUMN_BY_STATE[state]


def card_for(episode: int, title: str, status: WorkflowStatus) -> FlowCard:
    """单集的看板卡片：列取自状态名，``next_action`` 取下一步动作类型，有阻断即 ``blocked``。"""
    return FlowCard(
        episode=episode,
        title=title,
        column=column_for_state(status.state),
        next_action=status.next_action.type.value,
        blocked=bool(status.blockers),
    )


def build_flow_board(episodes: Iterable[tuple[int, str, WorkflowStatus]]) -> dict[str, Any]:
    """由各集 ``(集号, 标题, 状态)`` 构造看板：固定的列 + 每集一张卡片（按集号排序）。"""
    cards = [card_for(number, title, status) for number, title, status in sorted(episodes, key=lambda item: item[0])]
    return FlowBoard(
        columns=[FlowColumn(id=column_id, kind=kind) for column_id, kind in FLOW_COLUMNS],
        cards=cards,
    ).model_dump(mode="json")


__all__ = [
    "FLOW_COLUMNS",
    "FlowBoard",
    "FlowCard",
    "FlowColumn",
    "build_flow_board",
    "card_for",
    "column_for_state",
]
