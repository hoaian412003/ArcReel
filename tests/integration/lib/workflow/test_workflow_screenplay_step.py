"""编剧步骤在工作流状态里的位置：新一集先写剧本、审阅通过后才拆解单元；存量路径与广告不受影响。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.infra.json_io import atomic_write_json
from lib.script import screenplay
from lib.workflow.workflow_state import WorkflowStateService
from tests.integration.lib.workflow.test_workflow_state import (
    _make_project,
    _register_script_plan,
    _write_episode_source,
    _write_source_and_complete,
)

_SOURCE = "第一集原文"


def _draft() -> screenplay.ScreenplayDraft:
    return screenplay.ScreenplayDraft.model_validate(
        {
            "title": "第一集",
            "logline": "看点",
            "scenes": [
                {"heading": "内景 · 屋内 · 日", "summary": "概要", "estimated_seconds": 10, "text": "正文一"},
                {"heading": "外景 · 街头 · 夜", "summary": "概要", "estimated_seconds": 8, "text": "正文二"},
            ],
        }
    )


def _planned_episode(tmp_path: Path, mode: str = "narration", generation_mode: str = "storyboard"):
    pm, project_path = _make_project(tmp_path, mode, generation_mode=generation_mode)
    _write_source_and_complete(pm, project_path)
    _write_episode_source(project_path, 1, _SOURCE)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "title": "第一集",
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "planned",
                    "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 2},
                }
            ]
        ),
    )
    return pm, project_path


@pytest.mark.parametrize(
    ("mode", "generation_mode"),
    [
        ("narration", "storyboard"),
        ("drama", "storyboard"),
        ("narration", "reference_video"),
        ("drama", "reference_video"),
    ],
)
def test_a_new_episode_starts_with_the_screenwriter(tmp_path: Path, mode: str, generation_mode: str) -> None:
    pm, _ = _planned_episode(tmp_path, mode, generation_mode)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCREENPLAY"
    assert status.next_action.type == "write_screenplay"
    assert status.next_action.args == {"episode": 1}
    assert status.artifacts["screenplay"]["state"] == "missing"
    assert status.gates["screenplay_review"]["state"] == "pending"


def test_a_written_screenplay_waits_for_the_user_and_names_the_version_to_review(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    written = screenplay.write_screenplay(project_path, 1, _draft(), source_text=_SOURCE)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCREENPLAY_REVIEW"
    assert status.next_action.type == "review_screenplay"
    assert status.next_action.requires_confirmation is True
    assert status.next_action.args == {"episode": 1, "fingerprint": screenplay.screenplay_fingerprint(written)}
    assert status.artifacts["screenplay"]["state"] == "current"
    assert status.artifacts["screenplay"]["scene_count"] == 2
    assert status.artifacts["screenplay"]["total_seconds"] == 18


def test_an_approved_screenplay_hands_over_to_the_unit_breakdown(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    screenplay.write_screenplay(project_path, 1, _draft(), source_text=_SOURCE)
    screenplay.approve_screenplay(project_path, 1)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCRIPT_PLAN_CONTENT"
    assert status.next_action.type == "prepare_script_plan"
    assert status.next_action.args["preprocessor"] == "split-narration-segments"
    assert status.gates["screenplay_review"]["state"] == "approved"


def test_editing_an_approved_screenplay_sends_it_back_to_review(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    screenplay.write_screenplay(project_path, 1, _draft(), source_text=_SOURCE)
    screenplay.approve_screenplay(project_path, 1)
    screenplay.edit_scenes(project_path, 1, [{"op": "update", "index": 0, "fields": {"estimated_seconds": 12}}])

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCREENPLAY_REVIEW"
    assert status.next_action.type == "review_screenplay"


def test_a_screenplay_written_from_an_older_source_is_rewritten(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    screenplay.write_screenplay(project_path, 1, _draft(), source_text="分集重划之前的源文")
    screenplay.approve_screenplay(project_path, 1)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCREENPLAY"
    assert status.next_action.type == "write_screenplay"
    assert status.artifacts["screenplay"]["state"] == "stale"


def test_an_unreadable_screenplay_blocks_instead_of_being_overwritten(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    path = screenplay.screenplay_path(project_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCREENPLAY"
    assert status.next_action.type == "none"
    assert [blocker.code for blocker in status.blockers] == ["screenplay_corrupt"]


def test_an_episode_that_already_has_a_script_plan_keeps_the_original_path(tmp_path: Path) -> None:
    pm, project_path = _planned_episode(tmp_path)
    plan = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    plan.parent.mkdir(parents=True)
    atomic_write_json(plan, {"episode": 1, "segments": [{"segment_id": "E1S01"}]})
    _register_script_plan(project_path)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "SCRIPT_PLAN_REVIEW"
    assert status.next_action.type == "confirm_script_plan"
    assert "screenplay" not in status.artifacts


def test_ad_projects_have_no_screenplay_step(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    del project_path

    status = WorkflowStateService(pm).get_status("demo")

    assert "screenplay" not in status.artifacts
    assert status.state != "SCREENPLAY"
