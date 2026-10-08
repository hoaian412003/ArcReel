from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from lib.backends.audio_backends.base import VoiceOption
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.script.script_models import DubLine
from lib.speech.asr_runner import AsrError
from server.services import dub_pipeline
from server.services.tasks import dub_tasks
from server.services.tasks.dub_tasks import (
    dub_audio_resource_id,
    dub_line_audio_is_current,
    execute_dub_auto_task,
    execute_dub_tts_task,
    resolve_dub_voice,
)

UNIT_TEXT = "@[张三]{你好} {再见}"
VOICES = (
    VoiceOption(id="Nam Minh", label="Nam Minh", gender="male"),
    VoiceOption(id="Mai Anh", label="Mai Anh", gender="female"),
)


def test_voice_resolution_prefers_project_choice_then_catalog_then_fallback():
    assert resolve_dub_voice({"dub_voice_male": "Custom"}, "male", VOICES, "narrator") == "Custom"
    assert resolve_dub_voice({}, "female", VOICES, "narrator") == "Mai Anh"
    assert resolve_dub_voice({}, "male", (), "narrator") == "narrator"


def test_voice_resolution_prefers_the_neutral_default_pair_when_the_catalog_has_it():
    catalog = (
        VoiceOption(id="Adam bựa", label="Adam bựa", gender="male"),
        VoiceOption(id="Hải Đăng", label="Hải Đăng", gender="male"),
        VoiceOption(id="Trúc Ly", label="Trúc Ly", gender="female"),
        VoiceOption(id="Ngọc Trân", label="Ngọc Trân", gender="female"),
    )
    assert resolve_dub_voice({}, "male", catalog, "narrator") == "Hải Đăng"
    assert resolve_dub_voice({}, "female", catalog, "narrator") == "Ngọc Trân"


def test_audio_is_current_only_for_same_text_and_voice():
    line = DubLine(
        index=0,
        gender="male",
        text_vi="Xin chào",
        audio_path="audio/a.wav",
        audio_text="Xin chào",
        audio_voice="Nam Minh",
    )
    assert dub_line_audio_is_current(line, "Nam Minh")
    assert not dub_line_audio_is_current(line, "Other")
    assert not dub_line_audio_is_current(line.model_copy(update={"text_vi": "Khác"}), "Nam Minh")
    assert not dub_line_audio_is_current(line.model_copy(update={"audio_path": None}), "Nam Minh")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    proj = tmp_path / "projects" / "demo"
    (proj / "scripts").mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps(
            {
                "schema_version": 7,
                "title": "T",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "style": "s",
                "dub_target_language": "vi",
                "characters": {"张三": {"description": "x"}},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "text": UNIT_TEXT,
        "duration_seconds": 8,
        "dub_lines": [
            {"index": 0, "speaker": "张三", "gender": "male", "source_text": "你好", "text_vi": "Xin chào"},
            {"index": 1, "speaker": None, "gender": "female", "source_text": "再见", "text_vi": "Tạm biệt"},
        ],
    }
    (proj / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "summary": "x",
                "novel": {"title": "t", "chapter": "c"},
                "duration_seconds": 0,
                "video_units": [unit],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    migrate_v7_to_v8(proj)
    migrate_project_dir(proj)
    pm = ProjectManager(tmp_path)
    monkeypatch.setattr(dub_tasks, "get_project_manager", lambda: pm)
    return pm


def _install_fake_audio(monkeypatch: pytest.MonkeyPatch, pm: ProjectManager) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    project_dir = pm.get_project_path("demo")

    async def generate_audio_async(**kwargs: Any) -> tuple[Path, int]:
        calls.append(kwargs)
        target = project_dir / "audio" / f"segment_{kwargs['resource_id']}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RIFF")
        return target, 1

    ctx = SimpleNamespace(
        generator=SimpleNamespace(generate_audio_async=generate_audio_async),
        audio=SimpleNamespace(voices=VOICES, narration_voice="narrator"),
    )
    monkeypatch.setattr(dub_tasks, "resolve_generation_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(dub_tasks, "probe_audio_duration_seconds", AsyncMock(return_value=1.5))
    return calls


def _lines(pm: ProjectManager) -> list[dict[str, Any]]:
    script = pm.load_script("demo", "scripts/episode_1.json")
    return script["video_units"][0]["dub_lines"]


@pytest.mark.asyncio
async def test_synthesizes_each_line_with_its_gender_voice_and_registers_the_audio(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)

    result = await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="task-1")

    assert result["synthesized"] == [0, 1]
    assert [(c["text"], c["voice"], c["resource_id"]) for c in calls] == [
        ("Xin chào", "Nam Minh", dub_audio_resource_id("E1U1", 0)),
        ("Tạm biệt", "Mai Anh", dub_audio_resource_id("E1U1", 1)),
    ]
    first, second = _lines(project)
    assert first["audio_path"] == "audio/segment_dub__E1U1__0.wav"
    assert first["audio_duration_seconds"] == 1.5
    assert (first["audio_voice"], first["audio_text"]) == ("Nam Minh", "Xin chào")
    assert second["audio_voice"] == "Mai Anh"


@pytest.mark.asyncio
async def test_second_run_skips_lines_whose_audio_is_still_current(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)
    await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t1")
    calls.clear()

    result = await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t2")

    assert result["synthesized"] == []
    assert calls == []


@pytest.mark.asyncio
async def test_named_line_indexes_are_resynthesized_even_when_current(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)
    await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t1")
    calls.clear()

    result = await execute_dub_tts_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json", "line_indexes": [1]}, task_id="t2"
    )

    assert result["synthesized"] == [1]
    assert [c["text"] for c in calls] == ["Tạm biệt"]


@pytest.mark.asyncio
async def test_project_voice_choice_overrides_the_catalog(project: ProjectManager, monkeypatch: pytest.MonkeyPatch):
    calls = _install_fake_audio(monkeypatch, project)
    data = project.load_project("demo")
    data["dub_voice_male"] = "Hải Đăng"
    project.save_project("demo", data)

    await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t1")

    assert calls[0]["voice"] == "Hải Đăng"


@pytest.mark.asyncio
async def test_refuses_when_dubbing_is_not_enabled(project: ProjectManager, monkeypatch: pytest.MonkeyPatch):
    _install_fake_audio(monkeypatch, project)
    data = project.load_project("demo")
    data.pop("dub_target_language")
    project.save_project("demo", data)

    with pytest.raises(ValueError, match="未启用配音"):
        await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t1")


@pytest.mark.asyncio
async def test_synthesis_is_pinned_to_vieneu_whatever_the_project_audio_provider_is(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_audio(monkeypatch, project)
    data = project.load_project("demo")
    data["audio_backend"] = "dashscope/qwen3-tts-flash"
    project.save_project("demo", data)

    await execute_dub_tts_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, task_id="t1")

    resolve = cast(AsyncMock, dub_tasks.resolve_generation_context)
    pinned = resolve.await_args.args[1]
    assert pinned["audio_provider"] == "vieneu"
    assert pinned["audio_model"] == "vieneu-tts-v3-turbo"


# ---------------------------------------------------------------------------
# dub_auto：写台词 → 检测开始时间 → 合成
# ---------------------------------------------------------------------------

SCRIPT_PATH = "scripts/episode_1.json"


def _edit_script(pm: ProjectManager, mutate) -> None:
    path = pm.get_project_path("demo") / SCRIPT_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data["video_units"][0])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _give_video(pm: ProjectManager) -> None:
    video = pm.get_project_path("demo") / "reference_videos" / "E1U1.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    _edit_script(
        pm, lambda unit: unit.setdefault("generated_assets", {}).update(video_clip="reference_videos/E1U1.mp4")
    )


def _auto_payload(**extra: Any) -> dict[str, Any]:
    return {"script_file": SCRIPT_PATH, "episode": 1, **extra}


@pytest.mark.asyncio
async def test_auto_without_a_video_synthesizes_with_estimated_timing(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_audio(monkeypatch, project)
    detect = AsyncMock()
    monkeypatch.setattr(dub_tasks, "detect_start_times", detect)

    result = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")

    assert result["steps"] == {"lines": "existing", "timing": "no_video"}
    assert result["synthesized"] == [0, 1]
    detect.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_detects_start_times_from_the_video_before_synthesizing(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_audio(monkeypatch, project)
    _give_video(project)
    order: list[str] = []

    async def fake_detect(pm, project_name, script_file, unit_ids, *, language):
        order.append("detect")
        return [{"unit_id": unit_ids[0], "status": "detected", "detected": 2, "total": 2}]

    monkeypatch.setattr(dub_tasks, "detect_start_times", fake_detect)
    original = dub_tasks.resolve_generation_context

    async def tracking(*args: Any, **kwargs: Any):
        order.append("synthesize")
        return await original(*args, **kwargs)

    monkeypatch.setattr(dub_tasks, "resolve_generation_context", tracking)

    result = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")

    assert order == ["detect", "synthesize"]
    assert result["steps"]["timing"] == "detected"


@pytest.mark.asyncio
async def test_auto_skips_detection_when_asked_or_already_done(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_audio(monkeypatch, project)
    _give_video(project)
    detect = AsyncMock()
    monkeypatch.setattr(dub_tasks, "detect_start_times", detect)

    skipped = await execute_dub_auto_task("demo", "E1U1", _auto_payload(skip_timing=True), task_id="t")
    assert skipped["steps"]["timing"] == "skipped"

    def mark_detected(unit: dict[str, Any]) -> None:
        for line in unit["dub_lines"]:
            line["detected_start_seconds"] = 1.0

    _edit_script(project, mark_detected)
    existing = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")
    assert existing["steps"]["timing"] == "existing"
    detect.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_still_synthesizes_when_the_recogniser_is_unavailable(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)
    _give_video(project)
    monkeypatch.setattr(dub_tasks, "detect_start_times", AsyncMock(side_effect=AsrError("model download failed")))

    result = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")

    assert result["steps"]["timing"] == "unavailable"
    assert "model download failed" in result["steps"]["timing_error"]
    assert result["synthesized"] == [0, 1]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_auto_writes_the_vietnamese_lines_first_when_the_unit_has_none(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)
    saved_lines = json.loads((project.get_project_path("demo") / SCRIPT_PATH).read_text(encoding="utf-8"))[
        "video_units"
    ][0]["dub_lines"]
    _edit_script(project, lambda unit: unit.update(dub_lines=[]))

    async def fake_generate(project_name: str, episode: int, unit_ids: list[str], *, projects: ProjectManager):
        assert (project_name, episode, unit_ids) == ("demo", 1, ["E1U1"])
        _edit_script(projects, lambda unit: unit.update(dub_lines=saved_lines))

    import server.text_generation as text_generation

    monkeypatch.setattr(text_generation, "generate_reference_dub_lines", fake_generate)

    result = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")

    assert result["steps"]["lines"] == "written"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_auto_on_a_video_with_nothing_to_say_succeeds_without_doing_anything(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    calls = _install_fake_audio(monkeypatch, project)
    _edit_script(project, lambda unit: unit.update(text="只有画面，没有台词", dub_lines=[]))
    import server.text_generation as text_generation

    write_lines = AsyncMock()
    monkeypatch.setattr(text_generation, "generate_reference_dub_lines", write_lines)

    result = await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")

    assert result["steps"] == {"lines": "not_needed"}
    assert result["synthesized"] == []
    write_lines.assert_not_awaited()
    assert calls == []


@pytest.mark.asyncio
async def test_auto_fails_loudly_when_no_lines_could_be_written(
    project: ProjectManager, monkeypatch: pytest.MonkeyPatch
):
    _install_fake_audio(monkeypatch, project)
    _edit_script(project, lambda unit: unit.update(dub_lines=[]))
    import server.text_generation as text_generation

    monkeypatch.setattr(text_generation, "generate_reference_dub_lines", AsyncMock())

    with pytest.raises(ValueError, match="没有写出越南语台词"):
        await execute_dub_auto_task("demo", "E1U1", _auto_payload(), task_id="t")


# ---------------------------------------------------------------------------
# 并发：同时跑多个视频的配音时，剧本写入与语音识别各自串行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_edits_to_one_script_lose_nothing(project: ProjectManager):
    def add_one_second(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lines[0]["start_seconds"] = (lines[0].get("start_seconds") or 0) + 1.0
        return lines

    await asyncio.gather(
        *[dub_pipeline.update_unit_dub_lines(project, "demo", SCRIPT_PATH, "E1U1", add_one_second) for _ in range(6)]
    )

    assert _lines(project)[0]["start_seconds"] == 6.0


@pytest.mark.asyncio
async def test_only_one_recognition_runs_at_a_time(project: ProjectManager, monkeypatch: pytest.MonkeyPatch):
    _give_video(project)
    running = 0
    peak = 0

    async def slow_transcribe(files, *, language):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return {file: [] for file in files}

    monkeypatch.setattr(dub_pipeline, "transcribe_files", slow_transcribe)

    await asyncio.gather(
        *[dub_pipeline.detect_start_times(project, "demo", SCRIPT_PATH, ["E1U1"], language=None) for _ in range(4)]
    )

    assert peak == 1
