"""后端到队列服务的控制面：内嵌 worker 直接调，独立队列服务经总线通知。"""

from __future__ import annotations

from types import SimpleNamespace

from lib.infra.pg_bus import CHANNEL_CONTROL
from server import queue_control


class _Queue:
    def __init__(self) -> None:
        self.interrupted: list[str] = []

    async def mark_task_interrupted(self, task_id: str) -> int:
        self.interrupted.append(task_id)
        return 1


class _Worker:
    def __init__(self) -> None:
        self.forced: set[str] = set()
        self.reloads = 0

    async def force_cancel_running(self, task_ids: set[str]) -> None:
        self.forced = task_ids

    async def reload_limits(self) -> None:
        self.reloads += 1


def _request(worker=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(generation_worker=worker)))


def _capture_notify(monkeypatch) -> list[tuple[str, dict]]:
    sent: list[tuple[str, dict]] = []

    async def notify(channel: str, payload: dict | None = None) -> None:
        sent.append((channel, payload or {}))

    monkeypatch.setattr(queue_control, "notify", notify)
    return sent


async def test_force_cancel_uses_the_embedded_worker_when_present(monkeypatch):
    sent = _capture_notify(monkeypatch)
    worker, queue = _Worker(), _Queue()

    await queue_control.broadcast_force_cancel(_request(worker), queue, {"a"})

    assert worker.forced == {"a"}
    assert queue.interrupted == []
    assert sent == []


async def test_force_cancel_goes_over_the_bus_in_external_mode(monkeypatch):
    sent = _capture_notify(monkeypatch)
    monkeypatch.setenv("QUEUE_WORKER_MODE", "external")
    queue = _Queue()

    await queue_control.broadcast_force_cancel(_request(), queue, {"b", "a"})

    assert sent == [(CHANNEL_CONTROL, {"op": "force_cancel", "task_ids": ["a", "b"]})]
    assert queue.interrupted == []


async def test_force_cancel_without_any_worker_settles_tasks_in_place(monkeypatch):
    sent = _capture_notify(monkeypatch)
    monkeypatch.delenv("QUEUE_WORKER_MODE", raising=False)
    queue = _Queue()

    await queue_control.broadcast_force_cancel(_request(), queue, {"a"})

    assert queue.interrupted == ["a"]
    assert sent == []


async def test_config_change_reloads_the_embedded_worker_and_notifies_the_queue_service(monkeypatch):
    sent = _capture_notify(monkeypatch)
    worker = _Worker()

    await queue_control.broadcast_config_changed(_request(worker))

    assert worker.reloads == 1
    assert sent == [(CHANNEL_CONTROL, {"op": "reload_config"})]
