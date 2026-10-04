"""全局并发上限：跨 provider 的同媒体总占用不得超过管理面设定的上限，且改动即时生效。"""

from __future__ import annotations

import asyncio
from typing import Any

from lib.config.service import ConfigService
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import wait_for_task
from lib.generation.generation_worker import GLOBAL_CAP_SETTING_KEYS, CapacityTable, GenerationWorker, SlotTable
from tests.fakes import bind_safe_session_factory, refuse_resume_execution


async def _set_cap(factory, media: str, value: str) -> None:
    async with factory() as session:
        await ConfigService(session).set_setting(GLOBAL_CAP_SETTING_KEYS[media], value)
        await session.commit()


async def test_slot_table_total_spans_providers_of_one_media():
    slots = SlotTable()
    loop_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    slots.register("a", "image", "t1", loop_future)
    slots.register("b", "image", "t2", loop_future)
    slots.register("a", "video", "t3", loop_future)

    assert slots.occupied_total("image") == 2
    assert slots.occupied_total("video") == 1
    assert slots.occupied_total("audio") == 0


async def test_global_cap_limits_total_running_across_providers_and_follows_live_changes(file_db_factory, monkeypatch):
    bind_safe_session_factory(monkeypatch, file_db_factory)
    await _set_cap(file_db_factory, "image", "1")
    queue = GenerationQueue(session_factory=file_db_factory)
    running = 0
    peak = 0
    release = asyncio.Event()

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
        nonlocal running, peak
        del task, claimed_provider_id
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return {}

    async def provider(task: dict[str, Any]) -> str:
        return str(task["provider_id"])

    ids = []
    for index, provider_id in enumerate(("p1", "p2", "p1")):
        queued = await queue.enqueue_task(
            project_name="global-cap-missing-project",
            task_type="storyboard",
            media_type="image",
            resource_id=f"scene-{index}",
            provider_id=provider_id,
        )
        ids.append(queued["task_id"])
    worker = GenerationWorker(
        queue=queue,
        capacity=CapacityTable(_limits={"p1": {"image": 5}, "p2": {"image": 5}}, _defaults={"image": 5}),
        provider_projection=provider,
        executor=execute,
        lanes=("image",),
        resume_executor=refuse_resume_execution,
    )
    worker.poll_interval = 0.01
    worker.heartbeat_interval = 0.01
    await worker.start()
    try:
        await asyncio.sleep(0.3)
        assert running == 1
        statuses = [(await queue.get_task(t))["status"] for t in ids]
        assert statuses.count("queued") == 2

        # 放宽上限后下一轮配置刷新就让排队任务补位，不必重启。
        await _set_cap(file_db_factory, "image", "3")
        await worker.reload_limits()
        await asyncio.sleep(0.3)
        assert running == 3
    finally:
        release.set()
        await worker.stop()
    for task_id in ids:
        assert (await wait_for_task(task_id, 0.01, queue=queue))["status"] == "succeeded"
    assert peak == 3


async def test_cap_values_below_one_or_garbage_mean_unlimited(file_db_factory, monkeypatch):
    bind_safe_session_factory(monkeypatch, file_db_factory)
    for media, raw in (("image", "0"), ("video", "abc"), ("audio", "")):
        await _set_cap(file_db_factory, media, raw)
    worker = GenerationWorker(
        queue=GenerationQueue(session_factory=file_db_factory),
        executor=lambda *_a, **_k: asyncio.sleep(0, {}),
        resume_executor=refuse_resume_execution,
    )

    await worker.reload_limits()

    assert [worker.global_cap(m) for m in ("image", "video", "audio")] == [None, None, None]
