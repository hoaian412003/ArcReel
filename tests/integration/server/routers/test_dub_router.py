from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import dub as dub_router
from tests.auth_deps import AUTH_DEPENDENCIES

UNIT_TEXT = "@[张三]{你好} {再见}"


def _write_project(root: Path, *, dub_enabled: bool = True, units: list[dict[str, Any]] | None = None) -> None:
    proj = root / "demo"
    (proj / "scripts").mkdir(parents=True)
    project: dict[str, Any] = {
        "schema_version": 7,
        "title": "T",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "style": "s",
        "characters": {"张三": {"description": "x"}},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
    }
    if dub_enabled:
        project["dub_target_language"] = "vi"
    (proj / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
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
                "video_units": units or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _unit(**overrides: Any) -> dict[str, Any]:
    unit = {
        "unit_id": "E1U1",
        "text": UNIT_TEXT,
        "duration_seconds": 8,
        "dub_lines": [
            {"index": 0, "speaker": "张三", "gender": "male", "source_text": "你好", "text_vi": "Xin chào"},
            {"index": 1, "speaker": None, "gender": "female", "source_text": "再见", "text_vi": "Tạm biệt"},
        ],
        "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
    }
    unit.update(overrides)
    return unit


def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, vieneu_enabled: bool = True, **write_kwargs: Any
) -> tuple[TestClient, MagicMock]:
    _write_project(tmp_path / "projects", **write_kwargs)
    pm = ProjectManager(tmp_path)
    monkeypatch.setattr(dub_router, "get_project_manager", lambda: pm)
    queue = MagicMock()
    queue.enqueue_task = AsyncMock(side_effect=lambda **kw: {"task_id": f"t-{kw['resource_id']}"})
    monkeypatch.setattr(dub_router, "get_generation_queue", lambda: queue)
    monkeypatch.setattr(dub_router, "load_vieneu_config", AsyncMock(return_value=MagicMock(enabled=vieneu_enabled)))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(dub_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), queue


def test_timeline_lays_lines_out_sequentially_with_estimated_durations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, _ = _client(tmp_path, monkeypatch, units=[_unit()])

    body = client.get("/api/v1/projects/demo/dub/episodes/1/units/E1U1").json()

    assert body["video_path"] == "reference_videos/E1U1.mp4"
    assert body["original_volume"] == 0.5
    first, second = body["lines"]
    assert first["start_seconds"] == 0
    assert second["start_seconds"] > first["duration_seconds"]
    assert first["estimated"]
    assert second["estimated"]
    assert first["audio_path"] is None
    assert body["overflow_seconds"] == 0


def test_timeline_uses_real_audio_duration_and_honours_start_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unit = _unit()
    unit["dub_lines"][0].update(
        audio_path="audio/dub__E1U1__0.wav", audio_duration_seconds=2.0, audio_text="Xin chào", audio_voice="v"
    )
    unit["dub_lines"][1]["start_seconds"] = 5.0
    client, _ = _client(tmp_path, monkeypatch, units=[unit])

    first, second = client.get("/api/v1/projects/demo/dub/episodes/1/units/E1U1").json()["lines"]

    assert first["duration_seconds"] == 2.0
    assert not first["estimated"]
    assert first["audio_path"] == "audio/dub__E1U1__0.wav"
    assert second["start_seconds"] == 5.0


def test_audio_is_dropped_when_the_line_text_changed_since_synthesis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unit = _unit()
    unit["dub_lines"][0].update(audio_path="audio/x.wav", audio_duration_seconds=2.0, audio_text="Câu cũ")
    client, _ = _client(tmp_path, monkeypatch, units=[unit])

    first = client.get("/api/v1/projects/demo/dub/episodes/1/units/E1U1").json()["lines"][0]

    assert first["audio_path"] is None
    assert first["estimated"]


def test_stale_flag_follows_the_unit_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unit = _unit(text="@[张三]{你好吗} {再见}")
    client, _ = _client(tmp_path, monkeypatch, units=[unit])

    lines = client.get("/api/v1/projects/demo/dub/episodes/1/units/E1U1").json()["lines"]

    assert [line["stale"] for line in lines] == [True, False]


def test_dub_requires_the_project_to_enable_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, _ = _client(tmp_path, monkeypatch, dub_enabled=False, units=[_unit()])

    assert client.get("/api/v1/projects/demo/dub/episodes/1/units/E1U1").status_code == 409


def test_synthesize_enqueues_one_task_per_unit_with_dub_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plain = {"unit_id": "E1U2", "text": "x", "duration_seconds": 4}
    client, queue = _client(tmp_path, monkeypatch, units=[_unit(), plain])

    resp = client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["unit_ids"] == ["E1U1"]
    kwargs = queue.enqueue_task.await_args.kwargs
    assert kwargs["task_type"] == "dub_tts"
    assert kwargs["media_type"] == "audio"
    assert kwargs["provider_id"] == "vieneu"
    assert kwargs["payload"]["audio_provider"] == "vieneu"
    assert kwargs["payload"]["audio_model"] == "vieneu-tts-v3-turbo"
    assert kwargs["payload"]["script_file"] == "scripts/episode_1.json"


def test_synthesize_only_the_requested_units_and_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    other = _unit(unit_id="E1U2")
    client, queue = _client(tmp_path, monkeypatch, units=[_unit(), other])

    resp = client.post(
        "/api/v1/projects/demo/dub/episodes/1/synthesize", json={"unit_ids": ["E1U2"], "line_indexes": [1]}
    )

    assert resp.json()["unit_ids"] == ["E1U2"]
    assert queue.enqueue_task.await_count == 1
    assert queue.enqueue_task.await_args.kwargs["payload"]["line_indexes"] == [1]


def test_synthesize_rejects_unknown_unit_and_empty_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, queue = _client(tmp_path, monkeypatch, units=[_unit()])

    assert (
        client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={"unit_ids": ["NOPE"]}).status_code == 404
    )
    assert client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={"unit_ids": []}).status_code == 400
    queue.enqueue_task.assert_not_awaited()


def test_synthesize_without_any_dub_lines_is_a_bad_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, _ = _client(tmp_path, monkeypatch, units=[_unit(dub_lines=[])])

    assert client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={}).status_code == 400


def test_render_needs_a_video_and_synthesized_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, _ = _client(tmp_path, monkeypatch, units=[_unit()])

    assert client.post("/api/v1/projects/demo/dub/episodes/1/units/E1U1/render").status_code == 400


def test_synthesize_needs_no_general_audio_provider_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """配音固定走本地 VieNeu，项目 / 全局没配通用音频供应商也能创建。"""
    client, queue = _client(tmp_path, monkeypatch, units=[_unit()])

    resp = client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={})

    assert resp.status_code == 200, resp.text
    queue.enqueue_task.assert_awaited_once()


def test_synthesize_is_refused_with_a_clear_message_when_vieneu_is_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, queue = _client(tmp_path, monkeypatch, vieneu_enabled=False, units=[_unit()])

    resp = client.post("/api/v1/projects/demo/dub/episodes/1/synthesize", json={})

    assert resp.status_code == 409
    assert "VieNeu" in resp.json()["detail"]
    queue.enqueue_task.assert_not_awaited()
