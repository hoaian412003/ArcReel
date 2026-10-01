import asyncio
from datetime import datetime
from typing import Any

from lib.generation.generation_worker import CapacityTable, GenerationWorker
from tests.fakes import refuse_resume_execution


class _WakeQueue:
    def __init__(self) -> None:
        self.idle = asyncio.Event()
        self.completed = asyncio.Event()
        self.claimed = False
        self.succeeded: list[tuple[str, dict[str, Any]]] = []

    async def acquire_or_renew_worker_lease(self, **_kwargs: Any) -> bool:
        return True

    async def release_worker_lease(self, **_kwargs: Any) -> None:
        return None

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        return []

    async def claim_next_task(self, media_type: str, **_kwargs: Any) -> dict[str, str] | None:
        assert media_type == "text"
        if self.idle.is_set() and not self.claimed:
            self.claimed = True
            return {"task_id": "wake-task", "media_type": "text", "provider_id": "text"}
        self.idle.set()
        return None

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any]) -> int:
        self.succeeded.append((task_id, result))
        self.completed.set()
        return 1


async def test_wake_claims_task_without_waiting_for_poll_interval() -> None:
    queue = _WakeQueue()

    async def text_provider(_task: dict[str, Any]) -> str:
        return "text"

    async def execute(_task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, bool]:
        assert claimed_provider_id == "text"
        return {"ok": True}

    async def no_settle(*, taskless_started_before: datetime | None) -> int:
        return 0

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={}, _defaults={"text": 1}),
        provider_projection=text_provider,
        executor=execute,
        lanes=("text",),
        settle_interrupted_calls=no_settle,
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 60
    await worker.start()
    try:
        await asyncio.wait_for(queue.idle.wait(), timeout=1)
        worker.wake()
        await asyncio.wait_for(queue.completed.wait(), timeout=1)
        assert queue.succeeded == [("wake-task", {"ok": True})]
    finally:
        await worker.stop()


class _VideoQueue(_WakeQueue):
    def __init__(self, task_ids: list[str]) -> None:
        super().__init__()
        self.pending = list(task_ids)
        self.finished: list[str] = []

    async def claim_next_task(self, media_type: str, **_kwargs: Any) -> dict[str, str] | None:
        assert media_type == "video"
        if not self.pending or "p" in _kwargs.get("pool_full_providers", ()):
            return None
        return {"task_id": self.pending.pop(0), "media_type": "video", "task_type": "video", "provider_id": "p"}

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any]) -> int:
        self.finished.append(task_id)
        return 1


async def test_finished_slot_is_refilled_without_waiting_for_poll_interval() -> None:
    queue = _VideoQueue(["t1", "t2", "t3"])
    started: list[str] = []
    release = {tid: asyncio.Event() for tid in ("t1", "t2", "t3")}
    third_started = asyncio.Event()
    two_started = asyncio.Event()

    async def provider(_task: dict[str, Any]) -> str:
        return "p"

    async def execute(task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, bool]:
        started.append(task["task_id"])
        if len(started) == 2:
            two_started.set()
        if task["task_id"] == "t3":
            third_started.set()
        await release[task["task_id"]].wait()
        return {"ok": True}

    async def no_settle(*, taskless_started_before: datetime | None) -> int:
        return 0

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={"p": {"video": 2}}, _defaults={"video": 2}),
        provider_projection=provider,
        executor=execute,
        lanes=("video",),
        settle_interrupted_calls=no_settle,
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 60
    await worker.start()
    try:
        await asyncio.wait_for(two_started.wait(), timeout=1)
        assert "t3" not in started
        release["t1"].set()
        await asyncio.wait_for(third_started.wait(), timeout=1)
    finally:
        for event in release.values():
            event.set()
        await worker.stop()


class _CancelQueue(_VideoQueue):
    def __init__(self, task_ids: list[str]) -> None:
        super().__init__(task_ids)
        self.interrupted: list[str] = []

    async def mark_task_interrupted(self, task_id: str) -> int:
        self.interrupted.append(task_id)
        return 1


async def test_force_cancel_running_stops_local_tasks_and_marks_interrupted() -> None:
    queue = _CancelQueue(["t1", "t2"])
    started = asyncio.Event()
    cancelled: list[str] = []
    count = 0

    async def provider(_task: dict[str, Any]) -> str:
        return "p"

    async def execute(task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, bool]:
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(task["task_id"])
            raise
        return {"ok": True}

    async def no_settle(*, taskless_started_before: datetime | None) -> int:
        return 0

    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={"p": {"video": 2}}, _defaults={"video": 2}),
        provider_projection=provider,
        executor=execute,
        lanes=("video",),
        settle_interrupted_calls=no_settle,
        resume_executor=refuse_resume_execution,
    )
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await worker.force_cancel_running({"t1", "t2"})
        assert sorted(cancelled) == ["t1", "t2"]
        assert queue.interrupted.count("t1") >= 1
        assert queue.interrupted.count("t2") >= 1
    finally:
        await worker.stop()
