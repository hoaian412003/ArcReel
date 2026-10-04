"""对账：只把「无存活执行者且与供应商失联」的执行中任务判失败。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lib.db import async_session_factory
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit
from lib.generation.generation_queue import GenerationQueue
from lib.generation.queue_reconciler import (
    DEFAULT_SYNC_STALE_SECONDS,
    reconcile_stale_tasks,
)


async def _seed(*, synced_ago: timedelta | None, started_ago: timedelta = timedelta(hours=3)) -> str:
    task_id = f"rc-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id=f"r-{task_id}",
                status="running",
                queued_at=now - started_ago,
                started_at=now - started_ago,
                last_synced_at=None if synced_ago is None else now - synced_ago,
                updated_at=now,
            )
        )
        await session.commit()
    return task_id


async def _status(task_id: str) -> tuple[str, str | None]:
    async with async_session_factory() as session:
        task = (await session.execute(select(Task).where(Task.task_id == task_id))).scalar_one()
    return task.status, task.error_message


@pytest.mark.asyncio
async def test_stale_running_task_without_executor_is_failed_and_audited():
    task_id = await _seed(synced_ago=timedelta(seconds=DEFAULT_SYNC_STALE_SECONDS * 2))

    await reconcile_stale_tasks(GenerationQueue(), in_flight=set())

    status, error = await _status(task_id)
    assert status == "failed"
    assert error is not None
    assert error.startswith("[provider_sync_lost]")
    async with async_session_factory() as session:
        audit = (await session.execute(select(QueueAudit).where(QueueAudit.task_id == task_id))).scalar_one()
    assert (audit.action, audit.actor) == ("reconciled", "system")


@pytest.mark.asyncio
async def test_task_still_owned_by_a_live_worker_is_left_alone():
    task_id = await _seed(synced_ago=timedelta(hours=2))

    await reconcile_stale_tasks(GenerationQueue(), in_flight={task_id})

    assert (await _status(task_id))[0] == "running"


@pytest.mark.asyncio
async def test_recently_synced_task_is_left_alone():
    task_id = await _seed(synced_ago=timedelta(seconds=30))

    await reconcile_stale_tasks(GenerationQueue(), in_flight=set())

    assert (await _status(task_id))[0] == "running"


@pytest.mark.asyncio
async def test_never_synced_task_falls_back_to_started_at():
    stale = await _seed(synced_ago=None, started_ago=timedelta(hours=2))
    fresh = await _seed(synced_ago=None, started_ago=timedelta(seconds=20))

    await reconcile_stale_tasks(GenerationQueue(), in_flight=set())

    assert (await _status(stale))[0] == "failed"
    assert (await _status(fresh))[0] == "running"
