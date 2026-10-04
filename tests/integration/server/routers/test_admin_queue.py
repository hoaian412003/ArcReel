"""队列监控与审计 API：概览、任务明细与成本聚合、取消 / 人工判失败、并发上限。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from lib.db import async_session_factory
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit, TaskEvent
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import admin_queue
from tests.auth_deps import AUTH_DEPENDENCIES


def _app(role: str = "admin") -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="tester", role=role)
    app.include_router(admin_queue.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture
def admin_client():
    with TestClient(_app()) as c:
        yield c


@pytest.fixture
def project() -> str:
    return f"proj-{uuid.uuid4().hex[:8]}"


async def _seed(project: str, status: str, **extra) -> str:
    task_id = f"aq-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name=project,
                task_type="video",
                media_type="video",
                resource_id=f"r-{task_id}",
                status=status,
                provider_id="runninghub",
                queued_at=now,
                started_at=now if status != "queued" else None,
                updated_at=now,
                **extra,
            )
        )
        await session.commit()
    return task_id


async def _seed_call(task_id: str, project: str, *, cost: float, coins: float | None) -> None:
    async with async_session_factory() as session:
        session.add(
            ApiCall(
                project_name=project,
                call_type="video",
                model="m",
                status="success",
                task_id=task_id,
                started_at=datetime.now(UTC),
                cost_amount=cost,
                currency="USD",
                provider="runninghub",
                provider_coins=coins,
            )
        )
        await session.commit()


def test_non_admin_is_forbidden():
    with TestClient(_app(role="user")) as c:
        assert c.get("/api/v1/admin/queue/overview").status_code == 403


@pytest.mark.asyncio
async def test_task_list_filters_and_aggregates_cost_and_coins(admin_client, project):
    done = await _seed(project, "succeeded", estimated_cost_amount=1.0, estimated_currency="USD")
    await _seed_call(done, project, cost=0.4, coins=10.0)
    await _seed_call(done, project, cost=0.6, coins=5.0)
    await _seed(project, "queued")

    body = admin_client.get("/api/v1/admin/queue/tasks", params={"project": project}).json()
    assert body["total"] == 2
    row = next(r for r in body["items"] if r["task_id"] == done)
    assert (row["cost_amount"], row["currency"], row["provider_coins"]) == (1.0, "USD", 15.0)
    assert (row["estimated_cost_amount"], row["estimated_currency"]) == (1.0, "USD")

    queued = admin_client.get("/api/v1/admin/queue/tasks", params={"project": project, "status": "queued"}).json()
    assert [r["status"] for r in queued["items"]] == ["queued"]
    assert queued["items"][0]["cost_amount"] is None


@pytest.mark.asyncio
async def test_task_detail_returns_timeline_and_calls_in_order(admin_client, project):
    task_id = await _seed(project, "running", input_digest="d" * 64)
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        session.add_all(
            [
                TaskEvent(task_id=task_id, at=now, kind="running", percent=10.0),
                TaskEvent(task_id=task_id, at=now, kind="submitted", percent=30.0, data={"provider_job_id": "j"}),
            ]
        )
        await session.commit()
    await _seed_call(task_id, project, cost=0.5, coins=2.0)

    body = admin_client.get(f"/api/v1/admin/queue/tasks/{task_id}").json()

    assert body["task"]["input_digest"] == "d" * 64
    assert [e["kind"] for e in body["events"]] == ["running", "submitted"]
    assert body["events"][1]["data"] == {"provider_job_id": "j"}
    assert [(c["cost_amount"], c["provider_coins"]) for c in body["api_calls"]] == [(0.5, 2.0)]


def test_unknown_task_is_404(admin_client):
    assert admin_client.get("/api/v1/admin/queue/tasks/does-not-exist").status_code == 404


@pytest.mark.asyncio
async def test_cancel_queued_task_is_audited_and_running_task_is_refused(admin_client, project):
    queued = await _seed(project, "queued")
    running = await _seed(project, "running")

    ok = admin_client.post(f"/api/v1/admin/queue/tasks/{queued}/cancel")
    refused = admin_client.post(f"/api/v1/admin/queue/tasks/{running}/cancel")

    assert ok.status_code == 200
    assert ok.json()["status"] == "cancelled"
    assert refused.status_code == 409
    async with async_session_factory() as session:
        audit = (await session.execute(select(QueueAudit).where(QueueAudit.task_id == queued))).scalar_one()
    assert (audit.action, audit.actor, audit.detail) == ("cancel", "default", {"previous_status": "queued"})


@pytest.mark.asyncio
async def test_mark_failed_only_applies_to_running_and_keeps_the_reason(admin_client, project):
    running = await _seed(project, "running", provider_job_id="job-9")
    queued = await _seed(project, "queued")

    ok = admin_client.post(f"/api/v1/admin/queue/tasks/{running}/mark-failed", json={"reason": "provider lost it"})
    refused = admin_client.post(f"/api/v1/admin/queue/tasks/{queued}/mark-failed", json={"reason": "x"})
    empty = admin_client.post(f"/api/v1/admin/queue/tasks/{running}/mark-failed", json={"reason": ""})

    assert ok.status_code == 200
    assert ok.json()["status"] == "failed"
    assert "provider lost it" in ok.json()["error_message"]
    assert refused.status_code == 409
    assert empty.status_code == 422
    async with async_session_factory() as session:
        audit = (await session.execute(select(QueueAudit).where(QueueAudit.task_id == running))).scalar_one()
    assert audit.action == "mark_failed"
    assert audit.detail["provider_job_id"] == "job-9"


@pytest.mark.asyncio
async def test_audit_log_filters_by_action_and_project(admin_client, project):
    queued = await _seed(project, "queued")
    admin_client.post(f"/api/v1/admin/queue/tasks/{queued}/cancel")

    body = admin_client.get("/api/v1/admin/queue/audit", params={"action": "cancel", "project": project}).json()

    assert body["total"] == 1
    assert body["items"][0]["task_id"] == queued


@pytest.mark.asyncio
async def test_overview_reports_live_counts_and_lane_usage(admin_client, project):
    await _seed(project, "queued")
    await _seed(project, "running")

    body = admin_client.get("/api/v1/admin/queue/overview").json()

    assert body["counts"]["queued"] >= 1
    assert body["counts"]["running"] >= 1
    lane = next(lane for lane in body["lanes"] if lane["provider_id"] == "runninghub" and lane["media_type"] == "video")
    assert lane["queued"] >= 1
    assert lane["running"] >= 1
    assert body["oldest_queued_age_seconds"] is not None
    assert body["worker"]["mode"] in {"embedded", "external"}


def test_limits_roundtrip_caps_effective_lanes_and_clears(admin_client):
    try:
        saved = admin_client.put("/api/v1/admin/queue/limits", json={"overrides": {"video": 1}}).json()
        assert saved["overrides"] == {"image": None, "video": 1, "audio": None}
        video_lanes = [lane for lane in saved["effective"] if lane["media_type"] == "video"]
        assert video_lanes
        assert all(lane["limit"] <= 1 for lane in video_lanes)
        assert admin_client.get("/api/v1/admin/queue/limits").json()["overrides"]["video"] == 1
    finally:
        cleared = admin_client.put("/api/v1/admin/queue/limits", json={"overrides": {"video": None}}).json()
    assert cleared["overrides"]["video"] is None


@pytest.mark.parametrize("overrides", [{"video": 0}, {"video": -3}, {"text": 2}])
def test_limits_reject_invalid_values(admin_client, overrides):
    assert admin_client.put("/api/v1/admin/queue/limits", json={"overrides": overrides}).status_code == 400
