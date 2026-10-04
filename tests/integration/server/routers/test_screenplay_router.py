"""编剧剧本路由：读取、按场次修改、记录通过；与 Agent 工具共用同一服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from lib.script import screenplay
from server.error_handlers import register_error_handlers
from server.routers import screenplay as screenplay_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

BASE = "/api/v1/projects/demo/episodes/1/screenplay"


def _draft() -> screenplay.ScreenplayDraft:
    return screenplay.ScreenplayDraft.model_validate(
        {
            "title": "第一集",
            "logline": "看点",
            "scenes": [
                {"heading": "内景 · 屋内 · 日", "summary": "概要一", "estimated_seconds": 10, "text": "正文一"},
                {"heading": "外景 · 街头 · 夜", "summary": "概要二", "estimated_seconds": 8, "text": "正文二"},
            ],
        }
    )


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    return manager


@pytest.fixture
def screenplay_client(pm: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(screenplay_router, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(screenplay_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _seed(pm: ProjectManager) -> dict[str, Any]:
    return screenplay.write_screenplay(pm.get_project_path("demo"), 1, _draft())


def _patch(screenplay_client: TestClient, fingerprint: str, *operations: dict[str, Any]):
    return screenplay_client.patch(BASE, json={"base_fingerprint": fingerprint, "operations": list(operations)})


def test_reading_before_the_screenwriter_has_run_reports_none(screenplay_client: TestClient) -> None:
    response = screenplay_client.get(BASE)

    assert response.status_code == 200
    assert response.json()["status"] == "none"
    assert response.json()["scenes"] == []


def test_reading_returns_scenes_totals_markdown_and_the_review_status(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    _seed(pm)

    body = screenplay_client.get(BASE).json()

    assert body["status"] == "pending_review"
    assert body["total_seconds"] == 18
    assert body["scene_seconds"] == [10, 8]
    assert [scene["summary"] for scene in body["scenes"]] == ["概要一", "概要二"]
    assert "第 2 场" in body["markdown"]
    assert body["fingerprint"]


def test_an_unknown_episode_is_not_found(screenplay_client: TestClient) -> None:
    assert screenplay_client.get("/api/v1/projects/demo/episodes/9/screenplay").status_code == 404


def test_editing_one_scene_updates_the_total_and_returns_the_new_fingerprint(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    fingerprint = screenplay.screenplay_fingerprint(_seed(pm))

    response = _patch(screenplay_client, fingerprint, {"op": "update", "index": 1, "fields": {"estimated_seconds": 20}})

    assert response.status_code == 200
    assert response.json()["total_seconds"] == 30
    assert response.json()["fingerprint"] != fingerprint


def test_a_stale_fingerprint_is_a_conflict_and_changes_nothing(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    _seed(pm)

    response = _patch(screenplay_client, "stale", {"op": "set_meta", "fields": {"title": "不应落盘"}})

    assert response.status_code == 409
    assert screenplay_client.get(BASE).json()["title"] == "第一集"


def test_an_invalid_edit_is_unprocessable(screenplay_client: TestClient, pm: ProjectManager) -> None:
    fingerprint = screenplay.screenplay_fingerprint(_seed(pm))

    response = _patch(screenplay_client, fingerprint, {"op": "update", "index": 7, "fields": {"text": "x"}})

    assert response.status_code == 422


def test_an_unknown_operation_is_rejected_by_the_request_schema(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    fingerprint = screenplay.screenplay_fingerprint(_seed(pm))

    assert _patch(screenplay_client, fingerprint, {"op": "move", "index": 0}).status_code == 422


def test_approving_records_the_review_and_a_later_edit_revokes_it(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    fingerprint = screenplay.screenplay_fingerprint(_seed(pm))

    approved = screenplay_client.post(f"{BASE}/approve", json={"fingerprint": fingerprint})

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_at"]

    edited = _patch(screenplay_client, fingerprint, {"op": "update", "index": 0, "fields": {"text": "改过"}})
    assert edited.json()["status"] == "pending_review"


def test_approving_a_version_the_reviewer_did_not_see_is_a_conflict(
    screenplay_client: TestClient, pm: ProjectManager
) -> None:
    _seed(pm)

    assert screenplay_client.post(f"{BASE}/approve", json={"fingerprint": "not-seen"}).status_code == 409


def test_approving_without_a_screenplay_is_a_conflict(screenplay_client: TestClient) -> None:
    assert screenplay_client.post(f"{BASE}/approve", json={"fingerprint": "x"}).status_code == 409


def test_ad_projects_are_not_applicable(pm: ProjectManager, screenplay_client: TestClient) -> None:
    pm.update_project("demo", lambda project: project.__setitem__("content_mode", "ad"))

    assert screenplay_client.get(BASE).status_code == 409
