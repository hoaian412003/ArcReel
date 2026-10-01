from __future__ import annotations

import json
import shutil
import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import edit_timelines
from tests.auth_deps import AUTH_DEPENDENCIES

BASE = "/api/v1/projects/demo/episodes/1/edit-timelines"


def _make_video(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=160x90:d=2:r=24", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip


@pytest.fixture
def timeline_client(tmp_path, monkeypatch):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project_dir = pm.get_project_path("demo")
    (project_dir / "scripts/episode_1.json").write_text(
        json.dumps(
            {
                "video_units": [
                    {
                        "unit_id": "U1",
                        "duration_seconds": 2,
                        "generated_assets": {"video_clip": "reference_videos/U1.mp4"},
                    },
                    {"unit_id": "U2", "duration_seconds": 2, "generated_assets": {"video_clip": None}},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edit_timelines, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(edit_timelines.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    # 上下文管理器让所有请求共用一个事件循环，后台渲染任务才能跨请求存活。
    with TestClient(app) as test_client:
        test_client.project_dir = project_dir
        yield test_client


def test_create_edit_and_conflict(timeline_client):
    created = timeline_client.post(BASE, json={"name": "Cut A"}).json()
    tid = created["timeline"]["timeline_id"]
    assert [c["unit_id"] for c in created["timeline"]["revisions"][-1]["clips"]] == ["U1"]
    assert created["diagnostics"]["unused_unit_ids"] == ["U2"]
    assert created["final_cut"] is None

    clips = [{"clip_id": "x", "unit_id": "U1", "in_s": 0.5, "out_s": 1.5, "transition_to_next": "fade"}]
    updated = timeline_client.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": clips, "summary": "trim"})
    assert updated.status_code == 200
    assert updated.json()["timeline"]["revisions"][-1]["revision"] == 2

    stale = timeline_client.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": clips})
    assert stale.status_code == 409

    rolled = timeline_client.post(f"{BASE}/{tid}/rollback", json={"to_revision": 1, "parent_revision": 2})
    assert rolled.json()["timeline"]["revisions"][-1]["revision"] == 3

    assert timeline_client.patch(f"{BASE}/{tid}", json={"name": "Renamed"}).json()["timeline"]["name"] == "Renamed"
    assert [t["name"] for t in timeline_client.get(BASE).json()["timelines"]] == ["Renamed"]
    assert timeline_client.delete(f"{BASE}/{tid}").status_code == 200
    assert timeline_client.get(f"{BASE}/{tid}").status_code == 404


def test_copy_and_invalid_clip(timeline_client):
    tid = timeline_client.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
    copy = timeline_client.post(BASE, json={"name": "B", "source": "copy", "copy_from": tid})
    assert copy.json()["timeline"]["timeline_id"] != tid
    bad = timeline_client.put(
        f"{BASE}/{tid}",
        json={"parent_revision": 1, "clips": [{"clip_id": "a", "unit_id": "U1", "in_s": 3, "out_s": 1}]},
    )
    assert bad.status_code == 422


def test_missing_episode_is_404(timeline_client):
    assert timeline_client.get("/api/v1/projects/demo/episodes/9/edit-timelines").status_code == 404


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg required")
def test_render_produces_final_cut_and_goes_stale_after_edit(timeline_client):
    _make_video(timeline_client.project_dir / "reference_videos/U1.mp4")
    tid = timeline_client.post(BASE, json={"name": "A"}).json()["timeline"]["timeline_id"]
    assert timeline_client.post(f"{BASE}/{tid}/render").json()["status"] == "running"
    status = {}
    for _ in range(100):
        status = timeline_client.get(f"{BASE}/{tid}/render").json()
        if (status["render"] or {}).get("status") != "running":
            break
        time.sleep(0.1)
    assert status["render"]["status"] == "done"
    assert status["final_cut"]["stale"] is False
    assert (timeline_client.project_dir / f"final_cuts/ep1_{tid}.mp4").is_file()

    timeline_client.put(f"{BASE}/{tid}", json={"parent_revision": 1, "clips": [], "summary": "clear"})
    assert timeline_client.get(f"{BASE}/{tid}").json()["final_cut"]["stale"] is True
