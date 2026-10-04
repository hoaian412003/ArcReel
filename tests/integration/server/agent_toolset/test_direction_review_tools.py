"""导演审阅的 Agent 工具：读取报告与记录决定，与 REST 共用同一服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.project.project_manager import ProjectManager
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    DecideDirectionReviewRequest,
    DirectionReportRequest,
    ProjectScope,
    Services,
    ToolRequest,
    decide_direction_review,
    get_direction_report,
)
from tests.fakes import FakeConfigResolver


def _entry(number: int) -> dict[str, Any]:
    return {
        "segment_id": f"E1S0{number}",
        "duration_seconds": 4,
        "novel_text": f"第 {number} 段",
        "characters_in_segment": ["阿离"],
        "scenes": [],
        "props": [],
        "pending_authoring": True,
        "direction": {
            "camera": {"angle": "平视", "shot_size": "中景", "movement": "固定", "intent": "平静交代人物处境"},
            "performance": [{"character": "阿离", "note": "低头不语"}],
            "continuity_in": "屋檐下",
            "continuity_out": "屋檐下",
        },
    }


@pytest.fixture
def directed_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_character("demo", "阿离", "少女")
    manager.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    manager.update_project("demo", lambda project: project.__setitem__("generation_mode", "storyboard"))
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "director_plan": {
                "episode_intent": "交代处境",
                "arc": [{"beat": "平静", "units": [1]}, {"beat": "波动", "units": [2]}],
                "tone": "平静",
                "motifs": [],
            },
            "segments": [_entry(1), _entry(2)],
        },
        "episode_1.json",
    )
    return manager


@pytest.fixture
def services(directed_pm: ProjectManager) -> Services:
    return Services(
        projects=directed_pm,
        workflow_planner=WorkflowPlanner(directed_pm),
        capabilities=FakeConfigResolver(),
    )


def _scope(directed_pm: ProjectManager) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=directed_pm.data_root)


_CALLER = CallerContext(user_id="default", source="mcp")


async def test_get_direction_report_returns_the_status_and_report(
    directed_pm: ProjectManager, services: Services
) -> None:
    outcome = await get_direction_report(
        ToolRequest(DirectionReportRequest(episode=1)), _scope(directed_pm), _CALLER, services
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.status == "pending_review"
    assert outcome.value.report is not None
    assert [row["id"] for row in outcome.value.report["rows"]] == ["E1S01", "E1S02"]


async def test_decide_direction_review_records_the_decision(directed_pm: ProjectManager, services: Services) -> None:
    outcome = await decide_direction_review(
        ToolRequest(DecideDirectionReviewRequest(episode=1, decision="approve")), _scope(directed_pm), _CALLER, services
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.status == "approved"


async def test_decide_direction_review_reports_domain_refusals_as_problems(
    directed_pm: ProjectManager, services: Services
) -> None:
    outcome = await decide_direction_review(
        ToolRequest(DecideDirectionReviewRequest(episode=1, decision="changes")), _scope(directed_pm), _CALLER, services
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "direction_review_invalid_decision"


async def test_unknown_episode_is_a_problem_not_a_crash(directed_pm: ProjectManager, services: Services) -> None:
    outcome = await get_direction_report(
        ToolRequest(DirectionReportRequest(episode=7)), _scope(directed_pm), _CALLER, services
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "direction_review_episode_not_found"
