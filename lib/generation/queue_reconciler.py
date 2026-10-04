"""对账：把与供应商失联的执行中任务收口为失败。

只处理「没有任何存活执行者」的任务：``running`` 但不在本 worker 占用台账里，且最近一次与供应商同步
（``last_synced_at``，缺省回退 ``started_at``）早于阈值。仍在本 worker 内轮询的任务不由对账改判——
它们的协程稍后会写自己的终态，从外面改判会让产物与任务状态分叉；它们的失联由 ``/metrics`` 的
``arcreel_queue_stale_running`` 暴露。供应商侧可能仍在处理并计费，失败文案如实说明。
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from lib.config.service import ConfigService
from lib.db import safe_session_factory
from lib.db.base import utc_now
from lib.db.models.task import Task
from lib.generation.task_failure import encode_failure
from lib.generation.task_timeline import record_audit

if TYPE_CHECKING:
    from lib.generation.generation_queue import GenerationQueue

logger = logging.getLogger(__name__)

SETTING_SYNC_STALE_SECONDS = "queue_sync_stale_seconds"
DEFAULT_SYNC_STALE_SECONDS = 900
MIN_SYNC_STALE_SECONDS = 120


async def read_sync_stale_seconds() -> int:
    async with safe_session_factory() as session:
        raw = await ConfigService(session).get_setting(SETTING_SYNC_STALE_SECONDS, "")
    try:
        return max(MIN_SYNC_STALE_SECONDS, int(raw))
    except ValueError:
        return DEFAULT_SYNC_STALE_SECONDS


async def list_stale_running(*, stale_seconds: int, exclude: Collection[str] = ()) -> list[Task]:
    cutoff = utc_now() - timedelta(seconds=stale_seconds)
    async with safe_session_factory() as session:
        stmt = select(Task).where(
            Task.status == "running",
            func.coalesce(Task.last_synced_at, Task.started_at) < cutoff,
        )
        if exclude:
            stmt = stmt.where(Task.task_id.notin_(tuple(exclude)))
        return list((await session.execute(stmt)).scalars().all())


async def reconcile_stale_tasks(queue: GenerationQueue, *, in_flight: Collection[str]) -> int:
    """返回本轮改判为失败的任务数。"""
    stale_seconds = await read_sync_stale_seconds()
    stale = await list_stale_running(stale_seconds=stale_seconds, exclude=in_flight)
    marked = 0
    for task in stale:
        affected = await queue.mark_task_failed(
            task.task_id, encode_failure("provider_sync_lost", stale_seconds=stale_seconds)
        )
        if affected:
            marked += 1
            await record_audit(
                "reconciled",
                actor="system",
                task_id=task.task_id,
                project_name=task.project_name,
                task_type=task.task_type,
                detail={
                    "stale_seconds": stale_seconds,
                    "provider_id": task.provider_id,
                    "provider_job_id": task.provider_job_id,
                },
            )
            logger.warning("对账：任务 %s 与供应商失联超过 %ss，已标记失败", task.task_id, stale_seconds)
    return marked
