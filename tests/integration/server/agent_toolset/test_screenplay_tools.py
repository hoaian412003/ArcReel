"""编剧步骤：提交工具、剧本读写与审阅工具（Agent 与 REST 共用同一服务）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.project.project_manager import ProjectManager
from lib.script import screenplay
from server.services.project.workflow_planner import WorkflowPlanner
from server.tool_runtime import (
    ApproveScreenplayRequest,
    CallerContext,
    PatchScreenplayRequest,
    ProjectScope,
    ScreenplayRequest,
    Services,
    SubmitScreenplayRequest,
    ToolRequest,
    approve_screenplay,
    get_screenplay,
    patch_screenplay,
    submit_screenplay,
)
from tests.fakes import FakeConfigResolver


def _scene(**overrides: Any) -> dict[str, Any]:
    return {
        "heading": "内景 · 府门 · 日",
        "summary": "老管家送来亲笔信",
        "estimated_seconds": 12,
        "text": "老管家递上书信。",
        **overrides,
    }


def _model_output(**overrides: Any) -> str:
    return json.dumps(
        {
            "title": "边关来信",
            "logline": "一封信带来谜团",
            "scenes": [_scene(), _scene(estimated_seconds=8)],
            **overrides,
        },
        ensure_ascii=False,
    )


def _project(tmp_path: Path, content_mode: str = "drama", generation_mode: str = "storyboard") -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    pm.update_project("demo", lambda project: project.__setitem__("generation_mode", generation_mode))
    source = pm.get_project_path("demo") / "source"
    source.mkdir(exist_ok=True)
    (source / "episode_1.txt").write_text("阿离在府门前等来了老管家。", encoding="utf-8")
    return pm


def _services(pm: ProjectManager) -> Services:
    return Services(projects=pm, workflow_planner=WorkflowPlanner(pm), capabilities=FakeConfigResolver())


def _beat(kind: str = "dialogue", text: str = "你来了。", **extra: Any) -> dict[str, Any]:
    beat: dict[str, Any] = {"kind": kind, "text": text, **extra}
    if kind == "dialogue":
        beat.setdefault("speaker", "阿离")
    if kind == "action":
        beat.setdefault("seconds", 4)
    return beat


def _submission(*, scenes: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    default_scene = {
        "heading": "内景 · 府门 · 日",
        "summary": "老管家送来亲笔信",
        "beats": [_beat("action", "老管家递上书信。"), _beat(text="这是少爷的亲笔信。", speaker="阿离")],
    }
    return {
        "episode": 1,
        "title": "边关来信",
        "logline": "一封信带来谜团",
        "scenes": scenes or [default_scene],
        **overrides,
    }


async def _submit(pm: ProjectManager, **overrides: Any) -> Any:
    services = _services(pm)
    scope = ProjectScope(project_name="demo", data_root=pm.data_root)
    request = ToolRequest(SubmitScreenplayRequest.model_validate(_submission(**overrides)))
    return await submit_screenplay(request, scope, _CALLER, services)


@pytest.mark.parametrize(
    ("content_mode", "generation_mode"),
    [
        ("narration", "storyboard"),
        ("drama", "storyboard"),
        ("narration", "reference_video"),
        ("drama", "reference_video"),
    ],
)
async def test_the_screenwriter_submission_serves_every_non_ad_route(
    tmp_path: Path, content_mode: str, generation_mode: str
) -> None:
    pm = _project(tmp_path, content_mode, generation_mode)

    outcome = await _submit(pm)

    assert outcome.problem is None
    saved = screenplay.load_screenplay(pm.get_project_path("demo"), 1)
    assert saved is not None
    assert saved["scenes"][0]["text"] == "老管家递上书信。\n\n**阿离**: 这是少爷的亲笔信。"
    assert screenplay.review_status(saved) == "pending_review"
    assert saved["source_digest"] == screenplay.source_digest("阿离在府门前等来了老管家。")
    assert outcome.value.total_seconds == saved["scenes"][0]["estimated_seconds"]
    assert "已写入" in outcome.value.message


async def test_a_resubmission_replaces_the_screenplay_and_clears_the_review(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    await _submit(pm)
    screenplay.approve_screenplay(pm.get_project_path("demo"), 1)

    await _submit(pm, title="新版")

    saved = screenplay.load_screenplay(pm.get_project_path("demo"), 1)
    assert saved is not None
    assert saved["title"] == "新版"
    assert screenplay.review_status(saved) == "pending_review"


async def test_warnings_are_reported_but_the_screenplay_is_still_written(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    pm.update_project("demo", lambda project: project.__setitem__("episode_target_duration", 60))

    outcome = await _submit(pm)

    assert outcome.problem is None
    assert any("偏离目标 60 秒" in warning for warning in outcome.value.warnings)
    assert "质量检查未通过" in outcome.value.message
    assert screenplay.load_screenplay(pm.get_project_path("demo"), 1) is not None


async def test_an_episode_without_any_spoken_beat_is_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    scene = {"heading": "内景 · 府门 · 日", "summary": "无声", "beats": [_beat("action", "她站着。")]}

    outcome = await _submit(pm, scenes=[scene])

    assert outcome.problem is not None
    assert outcome.problem.code == "screenplay_rejected"
    assert "没有任何对白" in outcome.problem.detail
    assert screenplay.load_screenplay(pm.get_project_path("demo"), 1) is None


async def test_malformed_beats_are_rejected_by_the_request_schema() -> None:
    with pytest.raises(ValueError, match="speaker"):
        SubmitScreenplayRequest.model_validate(
            _submission(scenes=[{"heading": "h", "summary": "s", "beats": [{"kind": "dialogue", "text": "嗯"}]}])
        )


async def test_ad_projects_have_no_screenplay_step(tmp_path: Path) -> None:
    pm = _project(tmp_path, "ad")

    outcome = await _submit(pm)

    assert outcome.problem is not None
    assert "广告" in outcome.problem.detail


async def test_a_missing_source_is_reported_as_a_refusal(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    (pm.get_project_path("demo") / "source" / "episode_1.txt").unlink()

    outcome = await _submit(pm)

    assert outcome.problem is not None
    assert outcome.problem.code == "generation_refused"
    assert screenplay.load_screenplay(pm.get_project_path("demo"), 1) is None


@pytest.fixture
def screenplay_services(tmp_path: Path) -> Services:
    pm = _project(tmp_path)
    return Services(projects=pm, workflow_planner=WorkflowPlanner(pm), capabilities=FakeConfigResolver())


def _scope(ctx: Services) -> ProjectScope:
    return ProjectScope(project_name="demo", data_root=ctx.projects.data_root)


_CALLER = CallerContext(user_id="default", source="mcp")


def _seed(ctx: Services) -> dict[str, Any]:
    draft = screenplay.ScreenplayDraft.model_validate(json.loads(_model_output()))
    return screenplay.write_screenplay(ctx.projects.get_project_path("demo"), 1, draft)


async def test_get_screenplay_before_the_screenwriter_has_run(screenplay_services: Services) -> None:
    outcome = await get_screenplay(
        ToolRequest(ScreenplayRequest(episode=1)), _scope(screenplay_services), _CALLER, screenplay_services
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.status == "none"
    assert outcome.value.scenes == []


async def test_get_screenplay_reports_scenes_total_and_markdown(screenplay_services: Services) -> None:
    _seed(screenplay_services)

    outcome = await get_screenplay(
        ToolRequest(ScreenplayRequest(episode=1)), _scope(screenplay_services), _CALLER, screenplay_services
    )

    assert outcome.value is not None
    assert outcome.value.status == "pending_review"
    assert outcome.value.total_seconds == 20
    assert outcome.value.scene_seconds == [12, 8]
    assert outcome.value.fingerprint
    assert outcome.value.markdown is not None
    assert "第 2 场" in outcome.value.markdown


async def test_get_screenplay_for_an_unknown_episode_is_a_problem(screenplay_services: Services) -> None:
    outcome = await get_screenplay(
        ToolRequest(ScreenplayRequest(episode=9)), _scope(screenplay_services), _CALLER, screenplay_services
    )

    assert outcome.value is None
    assert outcome.problem is not None
    assert outcome.problem.code == "screenplay_episode_not_found"


async def test_patch_screenplay_edits_one_scene_and_reports_the_new_total(screenplay_services: Services) -> None:
    seeded = _seed(screenplay_services)

    outcome = await patch_screenplay(
        ToolRequest(
            PatchScreenplayRequest.model_validate(
                {
                    "episode": 1,
                    "base_fingerprint": screenplay.screenplay_fingerprint(seeded),
                    "operations": [{"op": "update", "index": 1, "fields": {"estimated_seconds": 30}}],
                }
            )
        ),
        _scope(screenplay_services),
        _CALLER,
        screenplay_services,
    )

    assert outcome.problem is None
    assert outcome.value is not None
    assert outcome.value.total_seconds == 42
    assert outcome.value.scene_seconds == [12, 30]


async def test_patch_screenplay_refuses_a_stale_fingerprint(screenplay_services: Services) -> None:
    _seed(screenplay_services)

    outcome = await patch_screenplay(
        ToolRequest(
            PatchScreenplayRequest.model_validate(
                {
                    "episode": 1,
                    "base_fingerprint": "stale",
                    "operations": [{"op": "set_meta", "fields": {"title": "x"}}],
                }
            )
        ),
        _scope(screenplay_services),
        _CALLER,
        screenplay_services,
    )

    assert outcome.value is None
    assert outcome.problem is not None
    assert outcome.problem.code == "screenplay_revision_conflict"


async def test_approving_records_the_review_and_an_edit_then_revokes_it(screenplay_services: Services) -> None:
    seeded = _seed(screenplay_services)
    fingerprint = screenplay.screenplay_fingerprint(seeded)

    approved = await approve_screenplay(
        ToolRequest(ApproveScreenplayRequest(episode=1, fingerprint=fingerprint)),
        _scope(screenplay_services),
        _CALLER,
        screenplay_services,
    )
    assert approved.value is not None
    assert approved.value.status == "approved"
    assert approved.value.approved_at

    edited = await patch_screenplay(
        ToolRequest(
            PatchScreenplayRequest.model_validate(
                {
                    "episode": 1,
                    "base_fingerprint": fingerprint,
                    "operations": [{"op": "update", "index": 0, "fields": {"text": "改过的正文"}}],
                }
            )
        ),
        _scope(screenplay_services),
        _CALLER,
        screenplay_services,
    )
    assert edited.value is not None
    assert edited.value.status == "pending_review"


async def test_approving_a_version_the_user_did_not_see_is_refused(screenplay_services: Services) -> None:
    _seed(screenplay_services)

    outcome = await approve_screenplay(
        ToolRequest(ApproveScreenplayRequest(episode=1, fingerprint="not-what-the-user-saw")),
        _scope(screenplay_services),
        _CALLER,
        screenplay_services,
    )

    assert outcome.value is None
    assert outcome.problem is not None
