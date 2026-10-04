"""入队入口：接受与拒绝都留审计，接受时写时间线、摘要与预估。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit, TaskEvent
from lib.generation.generation_queue import ActiveTaskRequestConflict, GenerationQueue
from lib.generation.queue_admission import SETTING_MAX_ESTIMATED_COST
from lib.infra.api_errors import UnprocessableError


async def _enqueue(queue: GenerationQueue, resource_id: str, **overrides):
    kwargs = {
        "project_name": "demo",
        "task_type": "storyboard",
        "media_type": "image",
        "resource_id": resource_id,
        "payload": {"prompt": "a cat"},
        "provider_id": "gemini-aistudio",
        "user_id": "default",
    }
    return await queue.enqueue_task(**{**kwargs, **overrides})


async def _audit_for(task_id: str) -> list[QueueAudit]:
    async with async_session_factory() as session:
        return list((await session.execute(select(QueueAudit).where(QueueAudit.task_id == task_id))).scalars())


@pytest.mark.asyncio
async def test_accepted_task_gets_digest_timeline_and_audit():
    queue = GenerationQueue()

    result = await _enqueue(queue, f"E1-{uuid.uuid4().hex[:8]}")

    task_id = result["task_id"]
    async with async_session_factory() as session:
        task = (await session.execute(select(Task).where(Task.task_id == task_id))).scalar_one()
        events = list((await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id))).scalars())
    assert task.input_digest
    assert len(task.input_digest) == 64
    assert [(e.kind, e.stage) for e in events] == [("queued", "admitted")]
    (audit,) = await _audit_for(task_id)
    assert audit.action == "enqueue_accepted"
    assert audit.detail["input_digest"] == task.input_digest
    assert audit.detail["source"] == "webui"


@pytest.mark.asyncio
async def test_deduped_enqueue_does_not_add_a_second_admission_record():
    queue = GenerationQueue()
    resource_id = f"E1-{uuid.uuid4().hex[:8]}"

    first = await _enqueue(queue, resource_id)
    again = await _enqueue(queue, resource_id)

    assert again["task_id"] == first["task_id"]
    assert again["deduped"]
    assert len(await _audit_for(first["task_id"])) == 1


@pytest.mark.asyncio
async def test_rejected_enqueue_is_audited_with_its_reason():
    queue = GenerationQueue()
    resource_id = f"E1-{uuid.uuid4().hex[:8]}"
    text = {"task_type": "text_outline", "media_type": "text", "provider_id": None}
    await _enqueue(queue, resource_id, payload={"prompt": "one"}, **text)

    with pytest.raises(ActiveTaskRequestConflict):
        await _enqueue(queue, resource_id, payload={"prompt": "two"}, **text)

    async with async_session_factory() as session:
        rejected = list(
            (
                await session.execute(
                    select(QueueAudit).where(
                        QueueAudit.action == "enqueue_rejected", QueueAudit.task_type == "text_outline"
                    )
                )
            ).scalars()
        )
    assert any(a.detail["reason"] == "ActiveTaskRequestConflict" and a.detail["input_digest"] for a in rejected)


@pytest.mark.asyncio
async def test_over_budget_task_is_cancelled_before_it_can_run_and_audited():
    async with async_session_factory() as session:
        await ConfigService(session).set_setting(SETTING_MAX_ESTIMATED_COST, "0.000001")
        await session.commit()
    try:
        queue = GenerationQueue()
        resource_id = f"E1-{uuid.uuid4().hex[:8]}"

        with pytest.raises(UnprocessableError) as raised:
            await _enqueue(
                queue,
                resource_id,
                task_type="video",
                media_type="video",
                payload={"duration_seconds": 8},
                script_file="s.json",
            )

        assert raised.value.key == "queue_budget_exceeded"
        async with async_session_factory() as session:
            task = (await session.execute(select(Task).where(Task.resource_id == resource_id))).scalar_one()
        assert task.status == "cancelled"
        reasons = [a.detail["reason"] for a in await _audit_for(task.task_id)]
        assert "budget_exceeded" in reasons
    finally:
        async with async_session_factory() as session:
            await ConfigService(session).set_setting(SETTING_MAX_ESTIMATED_COST, "")
            await session.commit()
