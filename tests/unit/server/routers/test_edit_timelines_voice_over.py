"""剪辑时间线里的越南语配音：开关、默认值、随修订保存、成片过期判断与渲染。"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from tests.auth_deps import AUTH_DEPENDENCIES

BASE = "/api/v1/projects/demo/episodes/1/edit-timelines"
FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=160x90:d=4:r=24",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-shortest", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=800:duration=1", str(path)],
        check=True,
    )


def _dub_unit(unit_id: str, *, with_audio: bool) -> dict[str, Any]:
    line: dict[str, Any] = {
        "index": 0,
        "speaker": None,
        "gender": "male",
        "source_text": "你好",
        "text_vi": "Xin chào",
    }
    if with_audio:
        line.update(
            audio_path=f"audio/{unit_id}.wav", audio_duration_seconds=1.0, audio_voice="v", audio_text="Xin chào"
        )
    return {
        "unit_id": unit_id,
        "text": "{你好}",
        "duration_seconds": 4,
        "generated_assets": {"video_clip": f"reference_videos/{unit_id}.mp4"},
        "dub_lines": [line],
    }


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dub_enabled: bool, audio: bool):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project = pm.load_project("demo")
    project["generation_mode"] = "reference_video"
    if dub_enabled:
        project["dub_target_language"] = "vi"
    pm.save_project("demo", project)
    project_dir = pm.get_project_path("demo")
    (project_dir / "scripts/episode_1.json").write_text(
        json.dumps({"video_units": [_dub_unit("U1", with_audio=audio), _dub_unit("U2", with_audio=False)]}),
        encoding="utf-8",
    )
    if audio:
        _make_wav(project_dir / "audio/U1.wav")
    monkeypatch.setattr(edit_timelines, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(edit_timelines.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as client:
        client.project_dir = project_dir
        yield client


@pytest.fixture
def with_dub(tmp_path, monkeypatch):
    yield from _client(tmp_path, monkeypatch, dub_enabled=True, audio=True)


@pytest.fixture
def dub_without_audio(tmp_path, monkeypatch):
    yield from _client(tmp_path, monkeypatch, dub_enabled=True, audio=False)


@pytest.fixture
def dub_off(tmp_path, monkeypatch):
    yield from _client(tmp_path, monkeypatch, dub_enabled=False, audio=True)


def _latest(detail: dict[str, Any]) -> dict[str, Any]:
    return detail["timeline"]["revisions"][-1]


def _wait_render(client, tid: str) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(300):
        status = client.get(f"{BASE}/{tid}/render").json()
        if (status["render"] or {}).get("status") != "running":
            break
        time.sleep(0.1)
    return status


class TestAvailability:
    def test_units_list_their_voice_over_lines_with_a_playable_url(self, with_dub):
        detail = with_dub.post(BASE, json={"name": "A"}).json()

        assert detail["voice_over_available"] is True
        assert detail["original_volume"] == 0.3
        units = {u["unit_id"]: u for u in detail["units"]}
        assert units["U1"]["dub_lines"] == [
            {
                "index": 0,
                "text_vi": "Xin chào",
                "start_s": 0.0,
                "duration_s": 1.0,
                "audio_url": "/api/v1/files/demo/audio/U1.wav",
            }
        ]
        assert units["U2"]["dub_lines"] == []

    def test_no_switch_when_the_project_has_no_voice_over_audio_yet(self, dub_without_audio):
        detail = dub_without_audio.post(BASE, json={"name": "A"}).json()

        assert detail["voice_over_available"] is False
        assert _latest(detail)["voice_over"] is False

    def test_no_switch_when_dubbing_is_off_for_the_project(self, dub_off):
        detail = dub_off.post(BASE, json={"name": "A"}).json()

        assert detail["voice_over_available"] is False
        assert all(u["dub_lines"] == [] for u in detail["units"])


class TestSwitch:
    def test_a_new_timeline_has_the_voice_over_on_when_audio_exists(self, with_dub):
        assert _latest(with_dub.post(BASE, json={"name": "A"}).json())["voice_over"] is True

    def test_the_switch_is_saved_with_the_revision_and_inherited_when_omitted(self, with_dub):
        tid = with_dub.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
        clips = [{"clip_id": "a", "unit_id": "U1"}]

        off = with_dub.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": clips, "voice_over": False}).json()
        assert _latest(off)["voice_over"] is False

        inherited = with_dub.put(f"{BASE}/{tid}", json={"parent_revision": 2, "clips": clips}).json()
        assert _latest(inherited)["voice_over"] is False

        on = with_dub.put(f"{BASE}/{tid}", json={"parent_revision": 3, "clips": clips, "voice_over": True}).json()
        assert _latest(on)["voice_over"] is True

    def test_rollback_and_copy_bring_the_switch_along(self, with_dub):
        tid = with_dub.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
        clips = [{"clip_id": "a", "unit_id": "U1"}]
        with_dub.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": clips, "voice_over": False})

        rolled = with_dub.post(f"{BASE}/{tid}/rollback", json={"to_revision": 1, "parent_revision": 2}).json()
        assert _latest(rolled)["voice_over"] is True

        copied = with_dub.post(BASE, json={"name": "B", "source": "copy", "copy_from": tid}).json()
        assert _latest(copied)["voice_over"] is True

    def test_an_old_timeline_file_without_the_field_loads_with_it_off(self, with_dub):
        tid = with_dub.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
        path = with_dub.project_dir / "timelines" / "1" / f"{tid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for revision in data["revisions"]:
            revision.pop("voice_over", None)
        path.write_text(json.dumps(data), encoding="utf-8")

        assert _latest(with_dub.get(f"{BASE}/{tid}").json())["voice_over"] is False


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
class TestRender:
    def _render(self, client) -> str:
        _make_video(client.project_dir / "reference_videos/U1.mp4")
        tid = client.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
        assert client.post(f"{BASE}/{tid}/render").json()["status"] == "running"
        assert _wait_render(client, tid)["render"]["status"] == "done"
        return tid

    def test_the_export_records_the_voice_over_it_was_mixed_with(self, with_dub):
        tid = self._render(with_dub)

        sidecar = json.loads((with_dub.project_dir / f"final_cuts/ep1_{tid}.json").read_text(encoding="utf-8"))
        assert isinstance(sidecar["voice_over"], str)
        assert with_dub.get(f"{BASE}/{tid}").json()["final_cut"]["stale"] is False

    def test_changing_the_voice_over_audio_makes_the_export_stale(self, with_dub):
        tid = self._render(with_dub)

        time.sleep(0.05)
        _make_wav(with_dub.project_dir / "audio/U1.wav")

        assert with_dub.get(f"{BASE}/{tid}").json()["final_cut"]["stale"] is True

    def test_switching_the_voice_over_off_makes_the_export_stale_and_the_next_one_clean(self, with_dub):
        tid = self._render(with_dub)
        clips = [{"clip_id": "c", "unit_id": "U1"}]
        with_dub.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": clips, "voice_over": False})
        assert with_dub.get(f"{BASE}/{tid}").json()["final_cut"]["stale"] is True

        assert with_dub.post(f"{BASE}/{tid}/render").json()["status"] == "running"
        assert _wait_render(with_dub, tid)["render"]["status"] == "done"

        sidecar = json.loads((with_dub.project_dir / f"final_cuts/ep1_{tid}.json").read_text(encoding="utf-8"))
        assert "voice_over" not in sidecar
        # 没混配音的成片不受之后配音音频变化的影响
        time.sleep(0.05)
        _make_wav(with_dub.project_dir / "audio/U1.wav")
        assert with_dub.get(f"{BASE}/{tid}").json()["final_cut"]["stale"] is False

    def test_an_export_without_any_voice_over_keeps_the_old_sidecar_shape(self, dub_off):
        tid = self._render(dub_off)

        sidecar = json.loads((dub_off.project_dir / f"final_cuts/ep1_{tid}.json").read_text(encoding="utf-8"))
        assert set(sidecar) == {"revision", "unit_versions"}
