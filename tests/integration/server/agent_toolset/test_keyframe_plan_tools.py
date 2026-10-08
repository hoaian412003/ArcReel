"""起始帧规划工具：简报、提交校验、落盘与沿用已审核格子（子智能体规划，服务端只校验）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.store import load_board, save_board
from lib.project.project_manager import ProjectManager
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    CallerContext,
    KeyframeBriefRequest,
    KeyframeCellInput,
    ProjectScope,
    Services,
    SubmitKeyframePlanRequest,
    ToolRequest,
    get_keyframe_brief,
    submit_keyframe_plan,
)
from tests.fakes import FakeConfigResolver

_CALLER = CallerContext(user_id="default", source="mcp")

_UNITS = [
    {"unit_id": "E1U01", "text": "@[车内] @[阿离] 看向 @[阿宁]。"},
    {"unit_id": "E1U02", "text": "@[车内] @[阿宁] 回头。"},
    {"unit_id": "E1U03", "text": "@[车内] @[阿离] 站起身。"},
]


def _script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "drama",
        "video_units": [{**unit, "duration_seconds": 8, "pending_authoring": True} for unit in _UNITS],
    }


def _make_project(tmp_path: Path, *, reference: bool = True) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.add_character("demo", "阿离", "少女，黑色短发")
    pm.add_character("demo", "阿宁", "少年，白T恤")
    pm.add_scenes_batch("demo", {"车内": {"description": "旧公交车内"}})
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")

    def _settings(project: dict[str, Any]) -> None:
        project["generation_mode"] = "reference_video" if reference else "storyboard"
        project["video_prompt_profile"] = "h3_ref2va"

    pm.update_project("demo", _settings)
    if reference:
        pm.save_script("demo", _script(), "episode_1.json")
    return pm


@pytest.fixture
def keyframe_services(tmp_path: Path) -> Services:
    pm = _make_project(tmp_path)
    return Services(projects=pm, workflow_planner=WorkflowPlanner(pm), capabilities=FakeConfigResolver())


def _scope(ctx: Services) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=ctx.projects.data_root)


def _cell(label: str, units: list[str], characters: list[str], **overrides: Any) -> KeyframeCellInput:
    fields: dict[str, Any] = {
        "label": label,
        "scene": "车内",
        "staging": "阿离在左、阿宁在右靠窗，并排坐着",
        "framing": "近景",
        "characters": characters,
        "unit_ids": units,
    }
    fields.update(overrides)
    return KeyframeCellInput(**fields)


async def _brief(ctx: Services):
    outcome = await get_keyframe_brief(ToolRequest(KeyframeBriefRequest(episode=1)), _scope(ctx), _CALLER, ctx)
    assert outcome.problem is None
    assert outcome.value is not None
    return outcome.value


async def _submit(ctx: Services, cells: list[KeyframeCellInput], base: str | None = None):
    request = SubmitKeyframePlanRequest(episode=1, cells=cells, base_fingerprint=base)
    return await submit_keyframe_plan(ToolRequest(request), _scope(ctx), _CALLER, ctx)


async def test_the_brief_carries_the_units_and_the_registered_names(keyframe_services: Services) -> None:
    brief = await _brief(keyframe_services)

    assert [u.unit_id for u in brief.units] == ["E1U01", "E1U02", "E1U03"]
    assert brief.units[0].text == "@[车内] @[阿离] 看向 @[阿宁]。"
    assert brief.units[0].direction == ""
    assert {c["name"] for c in brief.characters} == {"阿离", "阿宁"}
    assert [s["name"] for s in brief.scenes] == ["车内"]
    assert brief.cells_per_sheet == 4
    assert brief.existing_board is None
    assert brief.plan_fingerprint


async def test_a_valid_plan_is_saved_as_sheets_and_reports_uncovered_units(
    keyframe_services: Services, tmp_path: Path
) -> None:
    brief = await _brief(keyframe_services)

    outcome = await _submit(
        keyframe_services,
        [_cell("阿离近景", ["E1U01"], ["阿离", "阿宁"]), _cell("阿宁近景", ["E1U02"], ["阿宁"])],
        brief.plan_fingerprint,
    )

    assert outcome.problem is None
    state = outcome.value
    assert state is not None
    assert state.stale is False
    assert state.uncovered_unit_ids == ["E1U03"]
    assert [(s["id"], [c["id"] for c in s["cells"]]) for s in state.sheets] == [("S1", ["K01", "K02"])]
    board = load_board(keyframe_services.projects.get_project_path("demo"), 1)
    assert board is not None
    assert board.cell_for_unit("E1U02") is not None


async def test_an_invalid_plan_is_refused_with_every_problem_and_writes_nothing(
    keyframe_services: Services, tmp_path: Path
) -> None:
    outcome = await _submit(
        keyframe_services,
        [
            _cell("x", ["E1U02"], ["阿离"]),
            _cell(
                "y",
                ["E1U02"],
                ["阿宁"],
            ),
        ],
    )

    assert outcome.value is None
    assert outcome.problem is not None
    assert outcome.problem.code == "keyframe_invalid_plan"
    problems = outcome.problem.params["problems"]
    assert any("一个单元只用一格" in p for p in problems)
    assert any("没有 @ 引用" in p for p in problems)
    assert load_board(keyframe_services.projects.get_project_path("demo"), 1) is None


async def test_a_plan_built_on_an_older_brief_is_refused_as_stale(keyframe_services: Services) -> None:
    outcome = await _submit(
        keyframe_services, [_cell("阿离近景", ["E1U01"], ["阿离"])], base="not-the-current-fingerprint"
    )

    assert outcome.problem is not None
    assert outcome.problem.code == "keyframe_plan_stale"


async def test_resubmitting_an_unchanged_cell_keeps_its_image_and_review(
    keyframe_services: Services, tmp_path: Path
) -> None:
    project_path = keyframe_services.projects.get_project_path("demo")
    first = await _submit(keyframe_services, [_cell("阿离近景", ["E1U01"], ["阿离", "阿宁"])])
    assert first.problem is None
    image = project_path / "keyframes" / "episode_1" / "K01.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "gray").save(image)
    board = load_board(project_path, 1)
    assert board is not None
    cell = board.cells()[0]
    approved = KeyframeCell.model_validate(
        {**cell.model_dump(), "status": "approved", "image": "keyframes/episode_1/K01.png", "basis": "b1"}
    )
    save_board(
        project_path,
        KeyframeBoard(
            episode=1,
            plan_fingerprint=board.plan_fingerprint,
            sheets=[KeyframeSheet(id="S1", scene="车内", cells=[approved])],
        ),
    )

    again = await _submit(keyframe_services, [_cell("阿离近景", ["E1U01"], ["阿离", "阿宁"])])

    assert again.problem is None
    kept = load_board(project_path, 1)
    assert kept is not None
    assert kept.cells()[0].status == "approved"
    assert kept.cells()[0].image == "keyframes/episode_1/K01.png"
    assert kept.cells()[0].basis == "b1"


async def test_a_changed_cell_starts_over_without_the_old_image(keyframe_services: Services, tmp_path: Path) -> None:
    project_path = keyframe_services.projects.get_project_path("demo")
    await _submit(keyframe_services, [_cell("阿离近景", ["E1U01"], ["阿离", "阿宁"])])
    board = load_board(project_path, 1)
    assert board is not None
    approved = KeyframeCell.model_validate(
        {**board.cells()[0].model_dump(), "status": "approved", "image": "keyframes/episode_1/K01.png"}
    )
    save_board(
        project_path,
        KeyframeBoard(
            episode=1,
            plan_fingerprint=board.plan_fingerprint,
            sheets=[KeyframeSheet(id="S1", scene="车内", cells=[approved])],
        ),
    )

    outcome = await _submit(
        keyframe_services, [_cell("阿离近景", ["E1U01"], ["阿离", "阿宁"], staging="阿离在右、阿宁在左")]
    )

    assert outcome.problem is None
    changed = load_board(project_path, 1)
    assert changed is not None
    assert changed.cells()[0].status == "pending"
    assert changed.cells()[0].image is None
