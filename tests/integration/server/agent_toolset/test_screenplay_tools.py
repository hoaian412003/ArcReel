"""编剧步骤：生成工具、剧本读写与审阅工具（Agent 与 REST 共用同一服务）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.project.project_manager import ProjectManager
from lib.script import screenplay
from server.services.project.workflow_planner import WorkflowPlanner
from server.text_generation import TextGenerationError, TextGenerationRequest, generate_screenplay
from server.tool_runtime import (
    ApproveScreenplayRequest,
    CallerContext,
    PatchScreenplayRequest,
    ProjectScope,
    ScreenplayRequest,
    Services,
    ToolRequest,
    approve_screenplay,
    get_screenplay,
    patch_screenplay,
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


class _Generator:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []
        self.schemas: list[Any] = []

    async def generate(self, request, project_name=None):
        self.prompts.append(request.prompt)
        self.schemas.append(request.response_schema)

        class _Result:
            text = self.text

        return _Result()


def _use_generator(monkeypatch: pytest.MonkeyPatch, generator: _Generator) -> None:
    from server import text_generation as mod

    async def fake_create(_task_type, project_name=None, **_kwargs):
        return generator

    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)


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


async def _write(pm: ProjectManager, **request: Any) -> str:
    result = await generate_screenplay(
        TextGenerationRequest(episode=1, **request),
        project_name="demo",
        projects=pm,
        config_resolver=FakeConfigResolver(),
    )
    return result.message


@pytest.mark.parametrize(
    ("content_mode", "generation_mode"),
    [
        ("narration", "storyboard"),
        ("drama", "storyboard"),
        ("narration", "reference_video"),
        ("drama", "reference_video"),
    ],
)
async def test_the_screenwriter_serves_every_non_ad_route_with_one_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_mode: str, generation_mode: str
) -> None:
    pm = _project(tmp_path, content_mode, generation_mode)
    generator = _Generator(_model_output())
    _use_generator(monkeypatch, generator)

    message = await _write(pm)

    assert len(generator.prompts) == 1
    assert generator.schemas == [screenplay.ScreenplayDraft]
    assert "阿离在府门前等来了老管家" in generator.prompts[0]
    saved = screenplay.load_screenplay(pm.get_project_path("demo"), 1)
    assert saved is not None
    assert [scene["estimated_seconds"] for scene in saved["scenes"]] == [12, 8]
    assert screenplay.review_status(saved) == "pending_review"
    assert "约 20 秒" in message


async def test_a_rewrite_replaces_the_screenplay_and_clears_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    _use_generator(monkeypatch, _Generator(_model_output()))
    await _write(pm)
    screenplay.approve_screenplay(pm.get_project_path("demo"), 1)

    _use_generator(monkeypatch, _Generator(_model_output(title="新版")))
    await _write(pm)

    saved = screenplay.load_screenplay(pm.get_project_path("demo"), 1)
    assert saved is not None
    assert saved["title"] == "新版"
    assert screenplay.review_status(saved) == "pending_review"


async def test_dry_run_returns_the_prompt_without_calling_the_model_or_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    generator = _Generator(_model_output())
    _use_generator(monkeypatch, generator)

    message = await _write(pm, dry_run=True)

    assert message.startswith("DRY RUN")
    assert generator.prompts == []
    assert screenplay.load_screenplay(pm.get_project_path("demo"), 1) is None


async def test_unusable_model_output_fails_loudly_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    _use_generator(monkeypatch, _Generator(_model_output(scenes=[_scene(estimated_seconds=0)])))

    with pytest.raises(TextGenerationError, match="剧本"):
        await _write(pm)

    assert screenplay.load_screenplay(pm.get_project_path("demo"), 1) is None


async def test_ad_projects_have_no_screenplay_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path, "ad")
    _use_generator(monkeypatch, _Generator(_model_output()))

    with pytest.raises(TextGenerationError, match="广告"):
        await _write(pm)


async def test_a_missing_source_is_reported_as_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path)
    (pm.get_project_path("demo") / "source" / "episode_1.txt").unlink()
    _use_generator(monkeypatch, _Generator(_model_output()))

    with pytest.raises(TextGenerationError):
        await _write(pm)


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
