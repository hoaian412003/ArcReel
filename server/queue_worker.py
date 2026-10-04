"""独立队列服务入口：与 Web 后端分进程运行的 GenerationWorker。

    python -m server.queue_worker

后端只负责入队与读状态，执行、轮询、续跑、对账都在这里；后端重启、崩溃或发版不会打断在跑的任务。
进程内容：worker 主循环、PostgreSQL 通知监听（入队即醒、配置变更、强制中断）、项目事件转发、
``/metrics`` 与 ``/health`` 的监控端口。数据库迁移由后端负责，本进程只等待库升到最新版本。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from lib.db import close_db
from lib.generation.generation_worker import GenerationWorker
from lib.generation.queue_metrics import render_metrics
from lib.infra.httpx_shared import shutdown_http_client, startup_http_client
from lib.infra.logging_config import setup_logging
from lib.infra.pg_bus import CHANNEL_CONTROL, CHANNEL_WAKE, PgListener
from server.project_event_bridge import install_publisher
from server.services.tasks.generation_context import invalidate_backend_cache
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task

setup_logging(file=False)
logger = logging.getLogger("queue_worker")

_SCHEMA_WAIT_SECONDS = 600


async def _wait_for_schema() -> None:
    """等后端把库迁到最新版本；两个进程同时跑 Alembic 会互相踩。"""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from lib.db import async_engine

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    deadline = asyncio.get_running_loop().time() + _SCHEMA_WAIT_SECONDS
    while True:
        try:
            async with async_engine.connect() as conn:
                current = (await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
            if current == head:
                return
        except Exception:
            logger.info("等待数据库就绪…")
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("等待数据库迁移到最新版本超时")
        await asyncio.sleep(2.0)


def _build_monitor_app(worker: GenerationWorker) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:  # pyright: ignore[reportUnusedFunction]  # FastAPI 装饰器登记，无其它引用
        return await render_metrics(worker)

    @app.get("/health")
    async def health() -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]  # FastAPI 装饰器登记，无其它引用
        return {"status": "ok", "has_lease": worker._owns_lease, "inflight": len(worker.in_flight_task_ids())}

    return app


async def main() -> None:
    port = int(os.environ.get("QUEUE_METRICS_PORT", "9100"))
    await _wait_for_schema()
    await startup_http_client()

    loop = asyncio.get_running_loop()
    uninstall_publisher = install_publisher(loop)
    worker = GenerationWorker(
        executor=execute_generation_task,
        resume_executor=execute_resume_video_task,
        on_config_reload=invalidate_backend_cache,
    )

    listener = PgListener(
        {
            CHANNEL_WAKE: lambda _p: worker.wake(),
            CHANNEL_CONTROL: worker.handle_control,
        }
    )
    server = uvicorn.Server(uvicorn.Config(_build_monitor_app(worker), host="0.0.0.0", port=port, log_level="warning"))
    server_task = asyncio.create_task(server.serve(), name="queue-monitor")

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await worker.start()
    await listener.start()
    logger.info("队列服务已启动 (metrics :%d)", port)
    try:
        await stop.wait()
    finally:
        logger.info("队列服务停止中…")
        await listener.stop()
        await worker.stop()
        uninstall_publisher()
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await server_task
        await shutdown_http_client()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
