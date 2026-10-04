"""独立队列服务接收的控制消息：配置变更与强制中断。"""

from __future__ import annotations

import asyncio

from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from tests.fakes import bind_safe_session_factory, refuse_resume_execution


def _worker(queue: GenerationQueue, **kwargs) -> GenerationWorker:
    return GenerationWorker(
        queue=queue,
        executor=lambda *_a, **_k: asyncio.sleep(0, {}),
        resume_executor=refuse_resume_execution,
        **kwargs,
    )


async def test_reload_config_clears_execution_caches_then_reloads_limits(file_db_factory, monkeypatch):
    bind_safe_session_factory(monkeypatch, file_db_factory)
    calls: list[str] = []
    worker = _worker(GenerationQueue(session_factory=file_db_factory), on_config_reload=lambda: calls.append("cache"))
    reloaded = asyncio.Event()
    original = worker.reload_limits

    async def reload_limits() -> None:
        calls.append("limits")
        await original()
        reloaded.set()

    monkeypatch.setattr(worker, "reload_limits", reload_limits)

    await worker.handle_control({"op": "reload_config"})

    assert calls == ["cache", "limits"]
    assert reloaded.is_set()


async def test_resync_after_a_dropped_connection_reloads_like_a_config_change(file_db_factory, monkeypatch):
    bind_safe_session_factory(monkeypatch, file_db_factory)
    calls: list[str] = []
    worker = _worker(GenerationQueue(session_factory=file_db_factory), on_config_reload=lambda: calls.append("cache"))

    await worker.handle_control({"resync": True})

    assert calls == ["cache"]


async def test_force_cancel_only_acts_on_the_listed_tasks(file_db_factory, monkeypatch):
    forced: list[set[str]] = []
    worker = _worker(GenerationQueue(session_factory=file_db_factory))

    async def force_cancel_running(task_ids: set[str]) -> None:
        forced.append(task_ids)

    monkeypatch.setattr(worker, "force_cancel_running", force_cancel_running)

    await worker.handle_control({"op": "force_cancel", "task_ids": ["a", "b"]})
    await worker.handle_control({"op": "force_cancel", "task_ids": []})
    await worker.handle_control({"op": "something_else"})

    assert forced == [{"a", "b"}]
