"""generate_dub_voiceover / get_dub_status handler 的 ``ToolOutcome``。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.speech.vieneu_service import VieneuConfig
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    problem_of,
    run_declared_tool,
)

_TEXT = "海面\n{风从远方吹来。}\n{他转身离去。}"


def _line(index: int, source: str, text_vi: str, **extra: Any) -> dict[str, Any]:
    return {"index": index, "speaker": None, "gender": "male", "source_text": source, "text_vi": text_vi, **extra}


def _unit(unit_id: str, *, video: bool = True, lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "text": _TEXT,
        "duration_seconds": 10,
        "dub_lines": lines or [],
        "generated_assets": {"video_clip": f"videos/{unit_id}.mp4"} if video else {},
    }


def _dub_project(fake_ctx: ToolHarness, units: list[dict[str, Any]], *, dub: bool = True) -> None:
    project = fake_ctx.pm.project_payload
    project["generation_mode"] = "reference_video"
    if dub:
        project["dub_target_language"] = "vi"
    fake_ctx.pm.script_payload = {"content_mode": "narration", "episode": 1, "video_units": units}


async def _vieneu_on() -> VieneuConfig:
    return VieneuConfig.from_settings({})


async def _vieneu_off() -> VieneuConfig:
    return VieneuConfig.from_settings({"vieneu_enabled": "false"})


async def _generate(fake_ctx: ToolHarness, arguments: dict[str, Any] | None = None, **collaborators: Any):
    return await run_declared_tool(
        "generate_dub_voiceover",
        fake_ctx,
        {"script": "episode_1.json", **(arguments or {})},
        load_vieneu_config=_vieneu_on,
        **collaborators,
    )


async def _queued(fake_ctx: ToolHarness, task_type: str) -> list[dict[str, Any]]:
    return (await fake_ctx.queue.list_tasks(project_name="demo", task_type=task_type))["items"]


async def test_enqueues_one_auto_dub_task_per_unit_with_a_video(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1"), _unit("U2", video=False), _unit("U3")])

    out = await _generate(idle_fake_ctx, {"skip_timing": True})

    assert out.problem is None
    assert out.value is not None
    assert out.value["unit_ids"] == ["U1", "U3"]
    assert out.value["skipped"] == [{"unit_id": "U2", "reason": "no_video"}]
    tasks = await _queued(idle_fake_ctx, "dub_auto")
    assert sorted(task["resource_id"] for task in tasks) == ["U1", "U3"]
    assert {task["task_id"] for task in tasks} == set(out.value["task_ids"])
    assert all(task["payload"]["skip_timing"] is True and task["script_file"] == "episode_1.json" for task in tasks)
    assert all(task["source"] == "embedded" for task in tasks)


async def test_named_units_limit_the_queue(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1"), _unit("U3")])

    out = await _generate(idle_fake_ctx, {"unit_ids": ["U3"]})

    assert out.value is not None
    assert out.value["unit_ids"] == ["U3"]


async def test_unknown_unit_is_refused_without_queueing(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1")])

    out = await _generate(idle_fake_ctx, {"unit_ids": ["nope"]})

    assert problem_of(out).code == "unit_not_found"
    assert await _queued(idle_fake_ctx, "dub_auto") == []


async def test_units_without_video_queue_nothing(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1", video=False)])

    out = await _generate(idle_fake_ctx)

    assert problem_of(out).code == "dub_video_missing"


@pytest.mark.parametrize(
    ("arrange", "code"),
    [
        (lambda ctx: ctx.pm.project_payload.update(generation_mode="storyboard"), "dub_not_applicable"),
        (lambda ctx: ctx.pm.project_payload.pop("dub_target_language"), "dub_not_enabled"),
    ],
)
async def test_refuses_projects_that_do_not_dub(idle_fake_ctx: ToolHarness, arrange: Any, code: str) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1")])
    arrange(idle_fake_ctx)

    out = await _generate(idle_fake_ctx)

    assert problem_of(out).code == code
    assert await _queued(idle_fake_ctx, "dub_auto") == []


async def test_refuses_when_vieneu_is_disabled(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1")])

    out = await run_declared_tool(
        "generate_dub_voiceover",
        idle_fake_ctx,
        {"script": "episode_1.json"},
        load_vieneu_config=_vieneu_off,
    )

    assert problem_of(out).code == "dub_vieneu_disabled"
    assert await _queued(idle_fake_ctx, "dub_auto") == []


async def test_status_reports_one_state_per_unit(idle_fake_ctx: ToolHarness) -> None:
    first = _line(0, "风从远方吹来。", "Gió thổi từ phương xa.")
    second = _line(1, "他转身离去。", "Anh quay người đi.")
    ready = [
        {**first, "audio_path": "a0.wav", "audio_text": first["text_vi"], "audio_duration_seconds": 2.0},
        {**second, "audio_path": "a1.wav", "audio_text": second["text_vi"], "audio_duration_seconds": 2.0},
    ]
    needs_audio = [{**first, "detected_start_seconds": 1.0}, {**second, "detected_start_seconds": 4.0}]
    _dub_project(
        idle_fake_ctx,
        [
            _unit("no_video", video=False),
            _unit("no_lines"),
            _unit("timing", lines=[first, second]),
            _unit("audio", lines=needs_audio),
            _unit("ready", lines=ready),
        ],
    )

    out = await run_declared_tool("get_dub_status", idle_fake_ctx, {"script": "episode_1.json"})

    assert out.problem is None
    assert out.value is not None
    states = {unit["unit_id"]: unit["state"] for unit in out.value["units"]}
    assert states == {
        "no_video": "no_video",
        "no_lines": "no_lines",
        "timing": "needs_timing",
        "audio": "needs_audio",
        "ready": "ready",
    }
    by_id = {unit["unit_id"]: unit for unit in out.value["units"]}
    assert by_id["audio"]["lines"] == 2
    assert by_id["audio"]["detected_timing"] == 2
    assert by_id["audio"]["audio_current"] == 0
    assert by_id["ready"]["audio_current"] == 2
    assert by_id["ready"]["lines_expected"] == 2
    assert out.value["summary"] == {
        "units_total": 5,
        "with_video": 4,
        "no_video": 1,
        "no_lines": 1,
        "needs_timing": 1,
        "needs_audio": 1,
        "ready": 1,
        "complete": False,
    }
    assert out.value["active_tasks"] == {"running": False, "tasks": []}


async def test_status_flags_delay_and_overflow(idle_fake_ctx: ToolHarness) -> None:
    long_audio = {"audio_path": "a.wav", "audio_duration_seconds": 9.0}
    first = _line(0, "风从远方吹来。", "Gió thổi.", **long_audio, audio_text="Gió thổi.")
    second = _line(1, "他转身离去。", "Anh đi.", **long_audio, audio_text="Anh đi.", detected_start_seconds=1.0)
    _dub_project(idle_fake_ctx, [_unit("U1", lines=[first, second])])

    out = await run_declared_tool("get_dub_status", idle_fake_ctx, {"script": "episode_1.json"})

    assert out.value is not None
    unit = out.value["units"][0]
    assert unit["overflow_seconds"] > 0
    assert unit["delayed_lines"] == 1


async def test_status_sees_queued_dub_tasks(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1")])
    queued = await _generate(idle_fake_ctx)
    assert queued.value is not None

    out = await run_declared_tool("get_dub_status", idle_fake_ctx, {"script": "episode_1.json"})

    assert out.value is not None
    assert out.value["active_tasks"]["running"] is True
    assert [task["task_id"] for task in out.value["active_tasks"]["tasks"]] == queued.value["task_ids"]


async def test_status_refuses_projects_that_do_not_dub(idle_fake_ctx: ToolHarness) -> None:
    _dub_project(idle_fake_ctx, [_unit("U1")], dub=False)

    out = await run_declared_tool("get_dub_status", idle_fake_ctx, {"script": "episode_1.json"})

    assert problem_of(out).code == "dub_not_enabled"
