"""任务时间线与队列审计：事件追加、里程碑进度、轮询同步节流。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from lib.db import async_session_factory
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit, TaskEvent
from lib.generation.task_timeline import bind_task, note_provider_sync, record_audit, record_task_event, unbind_task


async def _seed_task(status: str = "running") -> str:
    task_id = f"tl-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id=f"r-{task_id}",
                status=status,
                queued_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return task_id


async def _events(task_id: str) -> list[TaskEvent]:
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id))
            ).scalars()
        )


async def _task(task_id: str) -> Task:
    async with async_session_factory() as session:
        return (await session.execute(select(Task).where(Task.task_id == task_id))).scalar_one()


@pytest.mark.asyncio
async def test_milestone_event_updates_task_progress_without_touching_sync():
    task_id = await _seed_task()

    await record_task_event(task_id, "submitted", stage="provider_submitted", data={"provider_job_id": "j1"})

    events = await _events(task_id)
    assert [(e.kind, e.percent, e.data) for e in events] == [("submitted", 30.0, {"provider_job_id": "j1"})]
    task = await _task(task_id)
    assert task.progress == 30.0
    assert task.last_synced_at is None


@pytest.mark.asyncio
async def test_touch_sync_stamps_last_synced_at():
    task_id = await _seed_task()

    await record_task_event(task_id, "running", touch_sync=True)

    assert (await _task(task_id)).last_synced_at is not None


@pytest.mark.asyncio
async def test_event_for_unknown_task_is_recorded_and_never_raises():
    await record_task_event("tl-missing-task", "progress", message="orphan")

    assert [e.message for e in await _events("tl-missing-task")] == ["orphan"]


@pytest.mark.asyncio
async def test_provider_sync_is_throttled_per_task_and_ignored_when_unbound():
    task_id = await _seed_task()

    await note_provider_sync(label="x")  # 未绑定任务：不写任何东西
    token = bind_task(task_id)
    try:
        await note_provider_sync(label="Ark")
        await note_provider_sync(label="Ark")  # 节流窗口内的第二次不再落库
    finally:
        unbind_task(token)

    events = await _events(task_id)
    assert [e.kind for e in events] == ["progress"]
    assert events[0].stage == "polling"
    assert (await _task(task_id)).last_synced_at is not None


@pytest.mark.asyncio
async def test_unbinding_resets_throttle_so_a_resumed_task_records_again():
    task_id = await _seed_task()

    for _ in range(2):
        token = bind_task(task_id)
        try:
            await note_provider_sync()
        finally:
            unbind_task(token)

    assert len(await _events(task_id)) == 2


@pytest.mark.asyncio
async def test_record_audit_persists_structured_detail():
    marker = f"audit-{uuid.uuid4().hex[:8]}"

    await record_audit("cancel", actor="u1", task_id=marker, project_name="demo", detail={"previous_status": "queued"})

    async with async_session_factory() as session:
        row = (await session.execute(select(QueueAudit).where(QueueAudit.task_id == marker))).scalar_one()
    assert (row.actor, row.action, row.project_name, row.detail) == (
        "u1",
        "cancel",
        "demo",
        {"previous_status": "queued"},
    )
