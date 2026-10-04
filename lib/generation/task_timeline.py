"""任务时间线与队列审计的写入入口。

时间线（``task_events``）按任务追加生命周期与进度事实；审计（``queue_audit``）记录准入决定与
管理操作。两者都是旁路记录：写入失败只记日志，绝不影响任务本身的状态迁移与计费。
"""

from __future__ import annotations

import contextvars
import logging
import time
from typing import Any

from sqlalchemy import update

from lib.db import safe_session_factory
from lib.db.base import utc_now
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit, TaskEvent

logger = logging.getLogger(__name__)

#: 里程碑进度：供应商不报百分比，不伪造连续值。
MILESTONE_PERCENT: dict[str, float] = {
    "queued": 0.0,
    "running": 10.0,
    "submitted": 30.0,
    "succeeded": 100.0,
}

#: 同一任务两次「轮询同步」写库的最小间隔，避免长轮询把库写成热点。
_SYNC_WRITE_MIN_INTERVAL_SECONDS = 5.0

_current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("timeline_task_id", default=None)
_last_sync_write: dict[str, float] = {}


def bind_task(task_id: str) -> contextvars.Token[str | None]:
    """把当前协程上下文绑定到任务，使轮询层不必知道任务 id 也能上报同步。"""
    return _current_task_id.set(task_id)


def unbind_task(token: contextvars.Token[str | None]) -> None:
    task_id = _current_task_id.get()
    _current_task_id.reset(token)
    if task_id:
        _last_sync_write.pop(task_id, None)


async def record_task_event(
    task_id: str,
    kind: str,
    *,
    stage: str | None = None,
    percent: float | None = None,
    message: str | None = None,
    data: dict[str, Any] | None = None,
    touch_sync: bool = False,
) -> None:
    """追加一条时间线事件，并按里程碑 / 同步时刻刷新任务行。失败只记日志。"""
    try:
        now = utc_now()
        milestone = percent if percent is not None else MILESTONE_PERCENT.get(kind)
        async with safe_session_factory() as session:
            session.add(
                TaskEvent(
                    task_id=task_id, at=now, kind=kind, stage=stage, percent=milestone, message=message, data=data
                )
            )
            values: dict[str, Any] = {}
            if milestone is not None:
                values["progress"] = milestone
            if touch_sync:
                values["last_synced_at"] = now
            if values:
                await session.execute(update(Task).where(Task.task_id == task_id).values(**values))
            await session.commit()
    except Exception:
        logger.warning("写入任务时间线失败 task=%s kind=%s", task_id, kind, exc_info=True)


async def note_provider_sync(*, label: str = "", status: str | None = None) -> None:
    """轮询层每拿到一次供应商响应调用一次：刷新 ``last_synced_at``，节流后追加一条 progress 事件。"""
    task_id = _current_task_id.get()
    if not task_id:
        return
    mono = time.monotonic()
    last = _last_sync_write.get(task_id)
    if last is not None and mono - last < _SYNC_WRITE_MIN_INTERVAL_SECONDS:
        return
    _last_sync_write[task_id] = mono
    await record_task_event(
        task_id,
        "progress",
        stage="polling",
        message=status or (f"{label} poll" if label else "poll"),
        percent=None,
        touch_sync=True,
    )


async def record_audit(
    action: str,
    *,
    actor: str,
    task_id: str | None = None,
    project_name: str | None = None,
    task_type: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """追加一条审计记录。失败只记日志。"""
    try:
        async with safe_session_factory() as session:
            session.add(
                QueueAudit(
                    at=utc_now(),
                    actor=actor,
                    action=action,
                    task_id=task_id,
                    project_name=project_name,
                    task_type=task_type,
                    detail=detail,
                )
            )
            await session.commit()
    except Exception:
        logger.warning("写入队列审计失败 action=%s task=%s", action, task_id, exc_info=True)
