from __future__ import annotations

import pytest

from lib.workflow.flow_board import FLOW_COLUMNS, build_flow_board, column_for_state
from lib.workflow.workflow_state import (
    WorkflowActionType,
    WorkflowBlocker,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowStatus,
)


def _status(state: str, action: str = "none", *, blocked: bool = False) -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "r",
            "source_revision": None,
            "project": WorkflowProject(content_mode="drama", generation_mode="storyboard", grid_storyboard=False),
            "target": None,
            "state": state,
            "blockers": [WorkflowBlocker(code="invalid_script", path="p", reason="x")] if blocked else [],
            "gates": {},
            "artifacts": {},
            "next_action": WorkflowNextAction(type=WorkflowActionType(action), reason="r"),
        }
    )


def test_columns_alternate_agent_and_human_units_in_production_order() -> None:
    assert FLOW_COLUMNS == (
        ("script_plan", "agent"),
        ("script_plan_review", "human"),
        ("direction", "agent"),
        ("direction_review", "human"),
        ("prompts", "agent"),
        ("assets", "agent"),
        ("video", "agent"),
        ("done", "agent"),
    )


@pytest.mark.parametrize(
    ("state", "column"),
    [
        ("PROJECT_INPUT", "script_plan"),
        ("ASSET_INVENTORY", "script_plan"),
        ("EPISODE_PLAN", "script_plan"),
        ("SCRIPT_PLAN_CONTENT", "script_plan"),
        ("SCRIPT_PLAN_REVIEW", "script_plan_review"),
        ("DIRECTOR_PLAN", "direction"),
        ("DIRECTOR_REVIEW", "direction_review"),
        ("FINAL_SCRIPT", "prompts"),
        ("ASSET_SHEETS", "assets"),
        ("STORYBOARD", "assets"),
        ("VIDEO", "video"),
        ("EXPORT_READY", "done"),
    ],
)
def test_every_workflow_state_maps_to_one_column(state: str, column: str) -> None:
    assert column_for_state(state) == column
    assert column in {column_id for column_id, _kind in FLOW_COLUMNS}


def test_board_has_one_card_per_episode_sorted_with_next_action_and_blocked_flag() -> None:
    board = build_flow_board(
        [
            (2, "第二集", _status("VIDEO", "generate_videos")),
            (1, "第一集", _status("DIRECTOR_REVIEW", "review_direction")),
            (3, "", _status("FINAL_SCRIPT", blocked=True)),
        ]
    )

    assert board["columns"] == [{"id": cid, "kind": kind} for cid, kind in FLOW_COLUMNS]
    assert board["cards"] == [
        {
            "episode": 1,
            "title": "第一集",
            "column": "direction_review",
            "next_action": "review_direction",
            "blocked": False,
        },
        {"episode": 2, "title": "第二集", "column": "video", "next_action": "generate_videos", "blocked": False},
        {"episode": 3, "title": "", "column": "prompts", "next_action": "none", "blocked": True},
    ]
