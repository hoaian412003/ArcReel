"""跨进程信号总线：PostgreSQL LISTEN/NOTIFY。

队列服务与后端是两个进程时，入队唤醒、配置变更、项目事件都靠它传递。载荷只是「刷新信号」：
真相始终在表里，丢一条信号只损失实时性（worker 仍按轮询间隔兜底，前端仍按轮询兜底）。
SQLite 部署没有跨进程场景，``notify`` 是空操作、监听器不启动。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text

from lib.db.engine import async_engine, get_database_url, is_sqlite_backend

logger = logging.getLogger(__name__)

CHANNEL_WAKE = "arcreel_queue_wake"
CHANNEL_CONTROL = "arcreel_queue_control"
CHANNEL_PROJECT = "arcreel_project_change"

#: PostgreSQL NOTIFY 载荷上限 8000 字节，留出余量。
_MAX_PAYLOAD_BYTES = 7500
_RECONNECT_DELAY_SECONDS = 3.0

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


async def notify(channel: str, payload: dict[str, Any] | None = None) -> None:
    """发一条信号；任何失败只记日志，不影响调用方的业务事务。"""
    if is_sqlite_backend():
        return
    body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode()) > _MAX_PAYLOAD_BYTES:
        body = json.dumps({"truncated": True})
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": body})
    except Exception:
        logger.warning("pg_notify 失败 channel=%s", channel, exc_info=True)


def _asyncpg_dsn() -> str:
    url = get_database_url()
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


class PgListener:
    """订阅若干 channel 的长连接；断线自动重连，重连后向处理器补发一次 ``{"resync": True}``。"""

    def __init__(self, handlers: dict[str, Handler]) -> None:
        self._handlers = handlers
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._dispatching: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if is_sqlite_backend() or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="pg-bus-listener")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _dispatch(self, channel: str, payload: dict[str, Any]) -> None:
        handler = self._handlers.get(channel)
        if handler is None:
            return
        try:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("pg_bus 处理器失败 channel=%s", channel)

    async def _run(self) -> None:
        import asyncpg

        first = True
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            conn = None
            try:
                conn = await asyncpg.connect(_asyncpg_dsn())

                def _on_notify(_c: Any, _pid: int, channel: str, body: str) -> None:
                    try:
                        payload = json.loads(body) if body else {}
                    except ValueError:
                        payload = {}
                    dispatch = loop.create_task(self._dispatch(channel, payload))
                    self._dispatching.add(dispatch)
                    dispatch.add_done_callback(self._dispatching.discard)

                for channel in self._handlers:
                    await conn.add_listener(channel, _on_notify)
                if not first:
                    for channel in self._handlers:
                        await self._dispatch(channel, {"resync": True})
                first = False
                while not self._stop.is_set() and not conn.is_closed():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("pg_bus 连接中断，稍后重连", exc_info=True)
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()
            if not self._stop.is_set():
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
