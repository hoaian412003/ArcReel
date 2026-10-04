"""单元拆解以已通过的剧本为依据：剧本进 prompt、对齐判定、违约落草稿、成功后自动确认内容。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.script import screenplay
from server import text_generation as mod
from server.text_generation import TextGenerationError, TextGenerationRequest, generate_narration_script_plan
from tests.integration.server.agent_tool_support import ToolHarness, nr_project, nr_segment

_SOURCE = "张三走向村口。他停下脚步，久久凝望。"


def _scene(heading: str, seconds: int, text: str) -> dict[str, Any]:
    return {"heading": heading, "summary": "概要", "estimated_seconds": seconds, "text": text}


def _seed_source(ctx: ToolHarness) -> None:
    nr_project(ctx)
    source = ctx.project_path / "source"
    source.mkdir(parents=True)
    (source / "episode_1.txt").write_text(_SOURCE, encoding="utf-8")


def _seed_screenplay(ctx: ToolHarness, *, approve: bool) -> None:
    draft = screenplay.ScreenplayDraft.model_validate(
        {
            "title": "村口",
            "logline": "一个人在村口驻足",
            "scenes": [
                _scene("外景 · 村口 · 日", 4, "张三走向村口。"),
                _scene("外景 · 村口 · 黄昏", 6, "他停下脚步，久久凝望。"),
            ],
        }
    )
    screenplay.write_screenplay(ctx.project_path, 1, draft)
    if approve:
        screenplay.approve_screenplay(ctx.project_path, 1)


class _Generator:
    def __init__(self, segments: list[dict[str, Any]]) -> None:
        self.segments = segments
        self.prompts: list[str] = []

    async def generate(self, request, project_name=None):
        self.prompts.append(request.prompt)

        class _Result:
            text = json.dumps({"segments": self.segments}, ensure_ascii=False)

        return _Result()


def _segments(*scene_numbers: int | None) -> list[dict[str, Any]]:
    texts = ["张三走向村口。", "他停下脚步，久久凝望。"]
    durations = [4, 6]
    segments = []
    for index, number in enumerate(scene_numbers):
        segment = nr_segment(f"E1S0{index + 1}", durations[index], texts[index])
        if number is not None:
            segment["scene_no"] = number
        segments.append(segment)
    return segments


@pytest.fixture
def confirmations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def fake_confirm(episode: int, **_kwargs: Any) -> mod.TextGenerationResult:
        calls.append(episode)
        return mod.TextGenerationResult(f"✅ 第 {episode} 集 script_plan 已确认")

    monkeypatch.setattr(mod, "confirm_script_review", fake_confirm)
    return calls


def _use(monkeypatch: pytest.MonkeyPatch, generator: Any) -> None:
    async def fake_create(_task_type, project_name=None, **_kwargs):
        return generator

    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)


async def _run(ctx: ToolHarness) -> str:
    result = await generate_narration_script_plan(
        TextGenerationRequest(episode=1),
        project_name="demo",
        projects=ctx.pm,
        config_resolver=ctx.config_resolver,
    )
    return result.message


async def test_an_approved_screenplay_reaches_the_prompt_and_the_plan_is_confirmed_automatically(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=True)
    generator = _Generator(_segments(1, 2))
    _use(monkeypatch, generator)

    message = await _run(fake_ctx)

    assert "已确认剧本" in generator.prompts[0]
    assert "外景 · 村口 · 黄昏" in generator.prompts[0]
    plan = json.loads((fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_segments.json").read_text("utf-8"))
    assert [segment["segment_id"] for segment in plan["segments"]] == ["E1S01", "E1S02"]
    assert all("scene_no" not in segment for segment in plan["segments"])
    assert confirmations == [1]
    assert "已确认" in message


async def test_without_a_screenplay_the_legacy_path_is_unchanged_and_not_auto_confirmed(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    generator = _Generator(_segments(None, None))
    _use(monkeypatch, generator)

    await _run(fake_ctx)

    assert "已确认剧本" not in generator.prompts[0]
    assert confirmations == []


async def test_a_screenplay_that_is_not_approved_blocks_the_breakdown(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=False)
    generator = _Generator(_segments(1, 2))
    _use(monkeypatch, generator)

    with pytest.raises(TextGenerationError, match="还没有通过审阅"):
        await _run(fake_ctx)

    assert generator.prompts == []
    assert confirmations == []


async def test_editing_the_screenplay_after_approval_blocks_the_breakdown_again(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=True)
    screenplay.edit_scenes(fake_ctx.project_path, 1, [{"op": "update", "index": 0, "fields": {"estimated_seconds": 9}}])
    _use(monkeypatch, _Generator(_segments(1, 2)))

    with pytest.raises(TextGenerationError, match="还没有通过审阅"):
        await _run(fake_ctx)


async def test_a_uncovered_scene_is_quarantined_for_repair_and_nothing_is_confirmed(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=True)
    _use(monkeypatch, _Generator(_segments(1, 1)))

    with pytest.raises(TextGenerationError, match="不得漏场"):
        await _run(fake_ctx)

    drafts = fake_ctx.project_path / "drafts" / "episode_1"
    assert (drafts / "script_plan_segments.invalid.json").exists()
    assert not (drafts / "script_plan_segments.json").exists()
    assert confirmations == []


async def test_missing_scene_numbers_are_a_violation_not_a_silent_pass(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=True)
    _use(monkeypatch, _Generator(_segments(None, None)))

    with pytest.raises(TextGenerationError, match="scene_no"):
        await _run(fake_ctx)


async def test_a_large_time_drift_is_reported_without_blocking(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    screenplay.write_screenplay(
        fake_ctx.project_path,
        1,
        screenplay.ScreenplayDraft.model_validate(
            {
                "title": "村口",
                "logline": "x",
                "scenes": [
                    _scene("外景 · 村口 · 日", 40, "张三走向村口。"),
                    _scene("外景 · 村口 · 黄昏", 6, "他停下脚步，久久凝望。"),
                ],
            }
        ),
    )
    screenplay.approve_screenplay(fake_ctx.project_path, 1)
    _use(monkeypatch, _Generator(_segments(1, 2)))

    message = await _run(fake_ctx)

    assert "剧本第 1 场预计 40 秒" in message
    assert confirmations == [1]


async def test_an_existing_formal_script_defers_the_confirmation_to_the_user(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts
) -> None:
    _seed_source(fake_ctx)
    _seed_screenplay(fake_ctx, approve=True)
    _use(monkeypatch, _Generator(_segments(1, 2)))

    async def overwrite_required(episode: int, **_kwargs: Any):
        raise mod.ScriptOverwriteRequiredError("需要覆盖确认", None)

    monkeypatch.setattr(mod, "confirm_script_review", overwrite_required)

    message = await _run(fake_ctx)

    assert "没有自动确认" in message
    assert "overwrite_revision" in message
    assert (Path(fake_ctx.project_path) / "drafts" / "episode_1" / "script_plan_segments.json").exists()


# ---------------------------------------------------------------------------
# drama / 参考生视频：同一套对齐判定
# ---------------------------------------------------------------------------


def _approve_two_scene_screenplay(ctx: ToolHarness, texts: tuple[str, str]) -> None:
    screenplay.write_screenplay(
        ctx.project_path,
        1,
        screenplay.ScreenplayDraft.model_validate(
            {
                "title": "山门",
                "logline": "归来",
                "scenes": [_scene("外景 · 山门 · 日", 4, texts[0]), _scene("外景 · 山门 · 夜", 4, texts[1])],
            }
        ),
    )
    screenplay.approve_screenplay(ctx.project_path, 1)


class _JsonGenerator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate(self, request, project_name=None):
        self.prompts.append(request.prompt)

        class _Result:
            text = json.dumps(self.payload, ensure_ascii=False)

        return _Result()


async def test_drama_dialogue_must_come_verbatim_from_the_approved_screenplay(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    from server.text_generation import generate_drama_script_plan
    from tests.integration.server.agent_tool_support import drama_project, drama_scene

    drama_project(fake_ctx)
    _approve_two_scene_screenplay(fake_ctx, ("**阿离**：我回来了。", "山门静悄悄。"))
    scenes = [
        {**drama_scene(), "scene_no": 1},
        {
            **drama_scene(
                scene_id="E1S02", utterances=[{"kind": "dialogue", "speaker": "阿离", "text": "我编的台词。"}]
            ),
            "scene_no": 2,
        },
    ]
    generator = _JsonGenerator({"title": "第一集", "scenes": scenes})
    _use(monkeypatch, generator)

    with pytest.raises(TextGenerationError, match="我编的台词"):
        await generate_drama_script_plan(
            TextGenerationRequest(episode=1),
            project_name="demo",
            projects=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )

    assert "已确认剧本" in generator.prompts[0]
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_normalized_script.invalid.json").exists()
    assert confirmations == []


async def test_drama_faithful_split_is_written_without_scene_numbers_and_confirmed(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    from server.text_generation import generate_drama_script_plan
    from tests.integration.server.agent_tool_support import drama_project, drama_scene

    drama_project(fake_ctx)
    _approve_two_scene_screenplay(fake_ctx, ("**阿离**：我回来了。", "山门静悄悄。"))
    scenes = [
        {**drama_scene(), "scene_no": 1},
        {**drama_scene(scene_id="E1S02", utterances=[], scene_description="山门静悄悄。"), "scene_no": 2},
    ]
    _use(monkeypatch, _JsonGenerator({"title": "第一集", "scenes": scenes}))

    await generate_drama_script_plan(
        TextGenerationRequest(episode=1),
        project_name="demo",
        projects=fake_ctx.pm,
        config_resolver=fake_ctx.config_resolver,
    )

    plan = json.loads(
        (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json").read_text("utf-8")
    )
    assert [scene["scene_id"] for scene in plan["scenes"]] == ["E1S01", "E1S02"]
    assert all("scene_no" not in scene for scene in plan["scenes"])
    assert confirmations == [1]


async def test_reference_video_dialogue_marks_must_come_from_the_approved_screenplay(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    from server.text_generation import generate_reference_script_plan
    from tests.integration.server.agent_tool_support import rv_director_plan, rv_source, rv_unit

    rv_source(fake_ctx)
    _approve_two_scene_screenplay(fake_ctx, ("**张三**：我在等人。", "张三在村口等人。"))
    units = [
        {**rv_unit("@[张三] 站在村口。\n@[张三]{我在等人。}"), "scene_no": 1},
        {**rv_unit("@[张三] 望向远方。\n@[张三]{这句不在剧本里}"), "scene_no": 2},
    ]
    generator = _JsonGenerator({"director_plan": rv_director_plan(2), "units": units})
    _use(monkeypatch, generator)

    with pytest.raises(TextGenerationError, match="这句不在剧本里"):
        await generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name="demo",
            projects=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )

    assert "已确认剧本" in generator.prompts[0]
    assert (fake_ctx.project_path / "drafts" / "episode_1" / "script_plan_reference_units.invalid.json").exists()
    assert confirmations == []


async def test_a_screenplay_written_from_an_older_source_blocks_the_breakdown(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, video_request_facts, confirmations: list[int]
) -> None:
    _seed_source(fake_ctx)
    screenplay.write_screenplay(
        fake_ctx.project_path,
        1,
        screenplay.ScreenplayDraft.model_validate(
            {
                "title": "村口",
                "logline": "x",
                "scenes": [
                    _scene("外景 · 村口 · 日", 4, "张三走向村口。"),
                    _scene("外景 · 村口 · 黄昏", 6, "他停下脚步。"),
                ],
            }
        ),
        source_text="分集被重划之前的源文",
    )
    screenplay.approve_screenplay(fake_ctx.project_path, 1)
    generator = _Generator(_segments(1, 2))
    _use(monkeypatch, generator)

    with pytest.raises(TextGenerationError, match="源文已经变化"):
        await _run(fake_ctx)

    assert generator.prompts == []
    assert confirmations == []
