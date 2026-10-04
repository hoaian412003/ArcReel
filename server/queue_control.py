"""后端与队列服务之间的控制面。

队列服务独立进程运行时，后端不能直接调用 worker：配置变更与强制中断改为经 PostgreSQL 通知总线下发。
内嵌 worker（SQLite / 单进程部署）仍直接调用；两路同时做，对方不存在时只是空操作。
"""

from __future__ import annotations

import os

from fastapi import Request

from lib.generation.generation_queue import GenerationQueue
from lib.infra.pg_bus import CHANNEL_CONTROL, notify


async def broadcast_config_changed(request: Request) -> None:
    """清后端缓存、刷新内嵌 worker 的限流，并通知独立队列服务做同样的事。"""
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()
    await notify(CHANNEL_CONTROL, {"op": "reload_config"})


async def broadcast_force_cancel(request: Request, queue: GenerationQueue, task_ids: set[str]) -> None:
    """强制中断执行中的任务：内嵌 worker 直接处理；独立队列服务经总线通知；两者都没有时就地落终态。"""
    worker = getattr(request.app.state, "generation_worker", None)
    if worker is not None:
        await worker.force_cancel_running(task_ids)
        return
    if os.environ.get("QUEUE_WORKER_MODE", "embedded").strip().lower() == "external":
        await notify(CHANNEL_CONTROL, {"op": "force_cancel", "task_ids": sorted(task_ids)})
        return
    for task_id in task_ids:
        await queue.mark_task_interrupted(task_id)
