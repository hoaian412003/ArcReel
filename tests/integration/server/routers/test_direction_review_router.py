"""导演审阅与 Flow 看板路由：读取状态与报告、记录决定、看板在项目被阻断时仍可读。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import record_migration_failure
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.error_handlers import register_error_handlers
from server.routers import direction_review as direction_router
from server.routers import flow_board as flow_board_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

BASE = "/api/v1/projects/demo"


def _direction(character: str = "阿离") -> dict[str, Any]:
    return {
        "camera": {"angle": "低角度", "shot_size": "特写", "movement": "缓慢推进", "intent": "托出对方压人的气势"},
        "performance": [{"character": character, "note": "语速放慢，目光不动"}],
        "continuity_in": "屋檐下",
        "continuity_out": "屋檐下",
    }


def _narration_script(*, directed: bool = True, pending: bool = True) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for number in (1, 2):
        segment: dict[str, Any] = {
            "segment_id": f"E1S0{number}",
            "duration_seconds": 4,
            "novel_text": f"第 {number} 段原文",
            "characters_in_segment": ["阿离"],
            "scenes": [],
            "props": [],
        }
        if pending:
            segment["pending_authoring"] = True
        else:
            segment["image_prompt"] = "画面"
            segment["video_prompt"] = "动作"
        if directed:
            segment["direction"] = _direction()
        segments.append(segment)
    script: dict[str, Any] = {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments}
    if directed:
        script["director_plan"] = {
            "episode_intent": "让观众感到压迫",
            "arc": [{"beat": "压迫", "units": [1]}, {"beat": "释然", "units": [2]}],
            "tone": "先压后松",
            "motifs": [],
        }
    return script


@pytest.fixture
def narration_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_character("demo", "阿离", "少女")
    manager.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    manager.update_project("demo", lambda project: project.__setitem__("generation_mode", "storyboard"))
    return manager


@pytest.fixture
def review_client(narration_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(direction_router, "get_project_manager", lambda: narration_pm)
    monkeypatch.setattr(flow_board_router, "get_project_manager", lambda: narration_pm)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(direction_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(flow_board_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _save(narration_pm: ProjectManager, script: dict[str, Any]) -> None:
    narration_pm.save_script("demo", script, "episode_1.json")


def _get(review_client: TestClient) -> dict[str, Any]:
    response = review_client.get(f"{BASE}/episodes/1/direction-review")
    assert response.status_code == 200, response.text
    return response.json()


def _decide(review_client: TestClient, **body: Any):
    return review_client.post(f"{BASE}/episodes/1/direction-review/decision", json=body)


class TestGetDirectionReview:
    def test_pending_review_returns_the_deterministic_report(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        state = _get(review_client)

        assert state["episode"] == 1
        assert state["status"] == "pending_review"
        assert state["fingerprint"]
        assert state["decided_at"] is None
        assert state["note"] is None
        report = state["report"]
        assert report["episode_intent"] == "让观众感到压迫"
        assert report["tone"] == "先压后松"
        assert [row["id"] for row in report["rows"]] == ["E1S01", "E1S02"]
        assert report["rows"][0]["camera"]["intent"] == "托出对方压人的气势"
        assert report["rows"][0]["performance"] == [{"character": "阿离", "note": "语速放慢，目光不动"}]
        assert report["angle_distribution"] == {"低角度": 2}
        assert report["warnings"] == []

    def test_pending_entries_without_director_output_are_no_direction(self, review_client, narration_pm):
        _save(narration_pm, _narration_script(directed=False))

        state = _get(review_client)

        assert state["status"] == "no_direction"
        assert state["report"] is None
        assert state["fingerprint"] is None

    def test_authored_episode_is_not_applicable(self, review_client, narration_pm):
        _save(narration_pm, _narration_script(directed=False, pending=False))

        assert _get(review_client)["status"] == "not_applicable"

    def test_ad_projects_are_not_applicable(self, review_client, narration_pm):
        narration_pm.update_project("demo", lambda project: project.__setitem__("content_mode", "ad"))

        state = _get(review_client)

        assert state["status"] == "not_applicable"
        assert state["report"] is None

    def test_unknown_episode_is_404(self, review_client):
        assert review_client.get(f"{BASE}/episodes/9/direction-review").status_code == 404

    def test_unknown_project_is_404(self, review_client):
        assert review_client.get("/api/v1/projects/missing/episodes/1/direction-review").status_code == 404


class TestDecideDirectionReview:
    def test_approve_records_the_decision_until_the_direction_changes(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        response = _decide(review_client, decision="approve")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "approved"
        assert body["decided_at"]
        edited = _narration_script()
        edited["segments"][0]["direction"]["camera"]["angle"] = "高角度"
        _save(narration_pm, edited)
        assert _get(review_client)["status"] == "pending_review"

    def test_approving_twice_is_refused(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())
        assert _decide(review_client, decision="approve").status_code == 200

        response = _decide(review_client, decision="approve")

        assert response.status_code == 409

    def test_changes_are_recorded_with_the_note_and_entry_ids(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        response = _decide(review_client, decision="changes", note="第二个镜头太平", entry_ids=["E1S02"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "changes_requested"
        assert body["note"] == "第二个镜头太平"
        stored = narration_pm.load_project("demo")["episodes"][0]["direction_review"]
        assert stored["decision"] == "changes"
        assert stored["entry_ids"] == ["E1S02"]

    def test_approving_after_changes_were_requested_is_allowed(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())
        _decide(review_client, decision="changes", note="再狠一点")

        assert _decide(review_client, decision="approve").json()["status"] == "approved"

    def test_redo_drops_entry_ids_and_the_note_is_optional(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        response = _decide(review_client, decision="redo", entry_ids=["E1S01"])

        assert response.status_code == 200, response.text
        assert narration_pm.load_project("demo")["episodes"][0]["direction_review"]["entry_ids"] is None

    def test_changes_need_a_note_or_entry_ids(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        assert _decide(review_client, decision="changes").status_code == 422

    def test_unknown_entry_ids_are_rejected(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        assert _decide(review_client, decision="changes", entry_ids=["E1S99"]).status_code == 422

    def test_decision_needs_director_output(self, review_client, narration_pm):
        _save(narration_pm, _narration_script(directed=False))

        assert _decide(review_client, decision="approve").status_code == 409

    def test_change_requests_are_refused_once_prompts_are_authored(self, review_client, narration_pm):
        _save(narration_pm, _narration_script(pending=False))

        assert _decide(review_client, decision="redo").status_code == 409

    def test_invalid_decision_value_is_422(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        assert _decide(review_client, decision="maybe").status_code == 422

    def test_not_applicable_projects_refuse_decisions(self, review_client, narration_pm):
        narration_pm.update_project("demo", lambda project: project.__setitem__("generation_mode", "reference_video"))

        assert _decide(review_client, decision="approve").status_code == 409


class TestFlowBoard:
    def test_board_lists_the_columns_and_one_card_per_episode(self, review_client, narration_pm):
        _save(narration_pm, _narration_script())

        response = review_client.get(f"{BASE}/flow-board")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [column["id"] for column in body["columns"]] == [
            "script_plan",
            "script_plan_review",
            "direction",
            "direction_review",
            "prompts",
            "assets",
            "video",
            "done",
        ]
        assert [column["kind"] for column in body["columns"]] == [
            "agent",
            "human",
            "agent",
            "human",
            "agent",
            "agent",
            "agent",
            "agent",
        ]
        assert [card["episode"] for card in body["cards"]] == [1]
        assert body["cards"][0]["title"] == "第一集"
        assert body["cards"][0]["column"] in {column["id"] for column in body["columns"]}

    def test_board_stays_readable_when_the_project_is_blocked(self, review_client, narration_pm):
        record_migration_failure(
            narration_pm.get_project_path("demo"),
            RuntimeError("清单预检失败"),
            schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
        )

        response = review_client.get(f"{BASE}/flow-board")

        assert response.status_code == 200, response.text
        card = response.json()["cards"][0]
        assert card["blocked"] is True
        assert card["next_action"] == "retry_project_migration"

    def test_unknown_project_is_404(self, review_client):
        assert review_client.get("/api/v1/projects/missing/flow-board").status_code == 404
