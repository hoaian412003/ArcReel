from __future__ import annotations

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
from server.services.tasks import dub_tasks
from server.services.tasks.dub_tasks import (
    dub_audio_resource_id,
    dub_line_audio_is_current,
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
        VoiceOption(id="Mai Anh", label="Mai Anh", gender="female"),
    )
    assert resolve_dub_voice({}, "male", catalog, "narrator") == "Hải Đăng"
    assert resolve_dub_voice({}, "female", catalog, "narrator") == "Mai Anh"


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
