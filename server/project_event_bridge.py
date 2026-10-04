"""把独立队列服务里发出的项目事件转送到后端的 SSE。

``project_change_hints`` 是进程内总线，队列服务进程里发的任务终态 / 用量事件到不了后端的
``ProjectEventService``。队列服务侧装发布器，把每条事件经 PostgreSQL 通知总线发出；后端侧装订阅
处理器，把收到的事件重新发进本进程总线。事件只是刷新信号：丢了前端轮询兜底，载荷超限也直接丢弃。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Any

from lib.infra.pg_bus import CHANNEL_PROJECT, notify
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    register_project_change_batch_listener,
    register_project_change_listener,
)

logger = logging.getLogger(__name__)

_MAX_HINT_PATHS = 40


def install_publisher(loop: asyncio.AbstractEventLoop) -> Callable[[], None]:
    """队列服务侧：本进程发出的项目事件一律转发上总线。返回卸载函数。"""

    def _send(payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(lambda: loop.create_task(notify(CHANNEL_PROJECT, payload)))

    def _on_batch(project: str, source: str, changes: Iterable[dict[str, Any]]) -> None:
        _send({"kind": "batch", "project": project, "source": source, "changes": [dict(c) for c in changes]})

    def _on_hint(project: str, source: str, paths: tuple[str, ...]) -> None:
        _send({"kind": "hint", "project": project, "source": source, "paths": list(paths)[:_MAX_HINT_PATHS]})

    unregister_batch = register_project_change_batch_listener(_on_batch)
    unregister_hint = register_project_change_listener(_on_hint)

    def uninstall() -> None:
        unregister_batch()
        unregister_hint()

    return uninstall


def handle_project_event(payload: dict[str, Any]) -> None:
    """后端侧：把总线上收到的项目事件重放进本进程总线。"""
    project = payload.get("project")
    if not isinstance(project, str) or not project or payload.get("truncated"):
        return
    source = payload.get("source") or "worker"
    if payload.get("kind") == "batch":
        emit_project_change_batch(project, payload.get("changes") or [], source=source)
    elif payload.get("kind") == "hint":
        emit_project_change_hint(project, source=source, changed_paths=payload.get("paths") or ())
