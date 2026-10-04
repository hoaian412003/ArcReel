"""队列监控与审计：概览、任务明细、审计日志、并发上限。

读路径直接查表（跨用户全量视图，仅管理员），写路径只有两个：取消排队中的任务、把失联的执行中
任务人工判失败，外加全局并发上限。每个写操作都落审计。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from lib.config.service import ConfigService
from lib.db import safe_session_factory
from lib.db.base import dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.models.task_event import QueueAudit, TaskEvent
from lib.db.repositories.task_repo import TaskNotCancellableError
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_worker import GLOBAL_CAP_SETTING_KEYS, CapacityTable
from lib.generation.task_failure import encode_failure, render_failure
from lib.generation.task_timeline import record_audit
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.infra.pg_bus import CHANNEL_CONTROL, notify
from server.auth import CurrentUser
from server.i18n import Translator

MEDIA_TYPES = ("image", "video", "audio")


async def require_admin(user: CurrentUser) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


router = APIRouter(prefix="/admin/queue", dependencies=[Depends(require_admin)])


def _env_defaults() -> dict[str, int]:
    def read(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    return {
        "image": read("IMAGE_MAX_WORKERS", 5),
        "video": read("VIDEO_MAX_WORKERS", 3),
        "audio": read("AUDIO_MAX_WORKERS", 10),
    }


async def _read_overrides() -> dict[str, int | None]:
    async with safe_session_factory() as session:
        settings = await ConfigService(session).get_all_settings()
    out: dict[str, int | None] = {}
    for media, key in GLOBAL_CAP_SETTING_KEYS.items():
        try:
            value = int(settings.get(key, ""))
        except ValueError:
            out[media] = None
            continue
        out[media] = value if value >= 1 else None
    return out


def _effective_limit(lane_limit: int, media: str, overrides: dict[str, int | None]) -> int:
    cap = overrides.get(media)
    return min(lane_limit, cap) if cap is not None else lane_limit


def _aware(value: datetime) -> datetime:
    """SQLite 取回聚合结果时丢掉时区；库里一律存 UTC，补回即可。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _counts(rows: Any) -> dict[str, int]:
    return {status: int(count) for status, count in rows}


def _cost_subquery():
    return (
        select(
            ApiCall.task_id.label("task_id"),
            func.sum(ApiCall.cost_amount).label("cost"),
            func.sum(ApiCall.provider_coins).label("coins"),
            func.max(ApiCall.currency).label("currency"),
        )
        .where(ApiCall.task_id.is_not(None))
        .group_by(ApiCall.task_id)
        .subquery()
    )


def _task_row(
    task: Task, cost: float | None, currency: str | None, coins: float | None, translate: Any
) -> dict[str, Any]:
    error = task.error_message
    return {
        "task_id": task.task_id,
        "project_name": task.project_name,
        "task_type": task.task_type,
        "media_type": task.media_type,
        "status": task.status,
        "provider_id": task.provider_id,
        "provider_job_id": task.provider_job_id,
        "progress": task.progress,
        "last_synced_at": dt_to_iso(task.last_synced_at),
        "queued_at": dt_to_iso(task.queued_at),
        "started_at": dt_to_iso(task.started_at),
        "finished_at": dt_to_iso(task.finished_at),
        "error_message": render_failure(error, translate) if error else None,
        "estimated_cost_amount": task.estimated_cost_amount,
        "estimated_currency": task.estimated_currency,
        "cost_amount": cost,
        "currency": currency,
        "provider_coins": coins,
        "user_id": task.user_id,
        "source": task.source,
    }


async def _load_task_row(task_id: str, translate: Any) -> dict[str, Any]:
    cost = _cost_subquery()
    async with safe_session_factory() as session:
        row = (
            await session.execute(
                select(Task, cost.c.cost, cost.c.currency, cost.c.coins)
                .outerjoin(cost, cost.c.task_id == Task.task_id)
                .where(Task.task_id == task_id)
            )
        ).first()
    if row is None:
        raise NotFoundError("queue_task_not_found")
    task, amount, currency, coins = row
    return _task_row(task, amount, currency, coins, translate)


@router.get("/overview")
async def overview() -> dict[str, Any]:
    queue = get_generation_queue()
    lease = await queue.get_worker_lease()
    now = utc_now()
    day_ago = now - timedelta(hours=24)
    overrides = await _read_overrides()
    table = await CapacityTable.from_db()

    async with safe_session_factory() as session:
        status_counts = _counts(
            (
                await session.execute(
                    select(Task.status, func.count())
                    .where(Task.status.in_(("queued", "running")))
                    .group_by(Task.status)
                )
            ).all()
        )
        finished = _counts(
            (
                await session.execute(
                    select(Task.status, func.count())
                    .where(Task.status.in_(("succeeded", "failed", "cancelled")), Task.finished_at >= day_ago)
                    .group_by(Task.status)
                )
            ).all()
        )
        lane_rows = (
            await session.execute(
                select(Task.provider_id, Task.media_type, Task.status, func.count())
                .where(Task.status.in_(("queued", "running")))
                .group_by(Task.provider_id, Task.media_type, Task.status)
            )
        ).all()
        oldest = (await session.execute(select(func.min(Task.queued_at)).where(Task.status == "queued"))).scalar()
        spend = (
            await session.execute(
                select(ApiCall.currency, func.coalesce(func.sum(ApiCall.cost_amount), 0.0))
                .where(ApiCall.started_at >= day_ago)
                .group_by(ApiCall.currency)
            )
        ).all()
        coins = (
            await session.execute(
                select(func.coalesce(func.sum(ApiCall.provider_coins), 0.0)).where(ApiCall.started_at >= day_ago)
            )
        ).scalar_one()

    lanes: dict[tuple[str, str], dict[str, Any]] = {}
    for provider, media, status, count in lane_rows:
        key = (provider or "unknown", media)
        lane = lanes.setdefault(
            key,
            {
                "provider_id": key[0],
                "media_type": media,
                "limit": _effective_limit(table.get(key[0], media), media, overrides),
                "running": 0,
                "queued": 0,
            },
        )
        lane[status] = count

    return {
        "worker": {
            "online": bool(lease and lease["is_online"]),
            "owner_id": lease["owner_id"] if lease else None,
            "lease_until": datetime.fromtimestamp(lease["lease_until"]).astimezone().isoformat() if lease else None,
            "mode": os.environ.get("QUEUE_WORKER_MODE", "embedded").strip().lower() or "embedded",
        },
        "counts": {
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "succeeded_24h": finished.get("succeeded", 0),
            "failed_24h": finished.get("failed", 0),
            "cancelled_24h": finished.get("cancelled", 0),
        },
        "lanes": sorted(lanes.values(), key=lambda lane: (lane["media_type"], lane["provider_id"])),
        "oldest_queued_age_seconds": max(0.0, (now - _aware(oldest)).total_seconds()) if oldest else None,
        "cost_24h": {
            "totals": [{"currency": currency, "amount": float(amount)} for currency, amount in spend],
            "provider_coins": float(coins or 0.0),
        },
    }


@router.get("/tasks")
async def list_tasks(
    _t: Translator,
    status: str | None = None,
    provider_id: str | None = None,
    media_type: str | None = None,
    project: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filters = []
    if status:
        filters.append(Task.status == status)
    if provider_id:
        filters.append(Task.provider_id == provider_id)
    if media_type:
        filters.append(Task.media_type == media_type)
    if project:
        filters.append(Task.project_name == project)
    if q:
        like = f"%{q}%"
        filters.append(or_(Task.task_id.ilike(like), Task.resource_id.ilike(like), Task.project_name.ilike(like)))
    cost = _cost_subquery()
    async with safe_session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(Task).where(*filters))).scalar_one()
        rows = (
            await session.execute(
                select(Task, cost.c.cost, cost.c.currency, cost.c.coins)
                .outerjoin(cost, cost.c.task_id == Task.task_id)
                .where(*filters)
                .order_by(Task.queued_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return {"items": [_task_row(t, c, cur, co, _t) for t, c, cur, co in rows], "total": total}


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str, _t: Translator) -> dict[str, Any]:
    task = await _load_task_row(task_id, _t)
    async with safe_session_factory() as session:
        digest = (await session.execute(select(Task.input_digest).where(Task.task_id == task_id))).scalar()
        events = (
            (await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id.asc())))
            .scalars()
            .all()
        )
        calls = (
            (
                await session.execute(
                    select(ApiCall).where(ApiCall.task_id == task_id).order_by(ApiCall.started_at.asc())
                )
            )
            .scalars()
            .all()
        )
    return {
        "task": {**task, "input_digest": digest},
        "events": [
            {
                "id": e.id,
                "at": dt_to_iso(e.at),
                "kind": e.kind,
                "stage": e.stage,
                "percent": e.percent,
                "message": e.message,
                "data": e.data,
            }
            for e in events
        ],
        "api_calls": [
            {
                "id": c.id,
                "call_type": c.call_type,
                "model": c.model,
                "status": c.status,
                "cost_amount": c.cost_amount,
                "currency": c.currency,
                "provider_coins": c.provider_coins,
                "started_at": dt_to_iso(c.started_at),
                "finished_at": dt_to_iso(c.finished_at),
                "error_message": c.error_message,
                "inputs": c.inputs,
            }
            for c in calls
        ],
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    before = await _load_task_row(task_id, _t)
    try:
        await get_generation_queue().cancel_task(task_id)
    except TaskNotCancellableError as exc:
        raise ConflictError("task_cancel_running") from exc
    await record_audit(
        "cancel",
        actor=user.id,
        task_id=task_id,
        project_name=before["project_name"],
        task_type=before["task_type"],
        detail={"previous_status": before["status"]},
    )
    return await _load_task_row(task_id, _t)


class MarkFailedBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/tasks/{task_id}/mark-failed")
async def mark_failed(task_id: str, body: MarkFailedBody, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    before = await _load_task_row(task_id, _t)
    if before["status"] != "running":
        raise ConflictError("queue_task_not_running")
    affected = await get_generation_queue().mark_task_failed(
        task_id, encode_failure("manually_marked_failed", reason=body.reason.strip())
    )
    if not affected:
        raise ConflictError("queue_task_not_running")
    await record_audit(
        "mark_failed",
        actor=user.id,
        task_id=task_id,
        project_name=before["project_name"],
        task_type=before["task_type"],
        detail={"reason": body.reason.strip(), "provider_job_id": before["provider_job_id"]},
    )
    return await _load_task_row(task_id, _t)


@router.get("/audit")
async def audit_log(
    action: str | None = None,
    user_id: str | None = None,
    project: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filters = []
    if action:
        filters.append(QueueAudit.action == action)
    if user_id:
        filters.append(QueueAudit.actor == user_id)
    if project:
        filters.append(QueueAudit.project_name == project)
    async with safe_session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(QueueAudit).where(*filters))).scalar_one()
        rows = (
            (
                await session.execute(
                    select(QueueAudit).where(*filters).order_by(QueueAudit.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": r.id,
                "at": dt_to_iso(r.at),
                "actor": r.actor,
                "action": r.action,
                "task_id": r.task_id,
                "project_name": r.project_name,
                "task_type": r.task_type,
                "detail": r.detail or {},
            }
            for r in rows
        ],
        "total": total,
    }


async def _limits_payload() -> dict[str, Any]:
    overrides = await _read_overrides()
    table = await CapacityTable.from_db()
    effective = [
        {"provider_id": provider, "media_type": media, "limit": _effective_limit(limit, media, overrides)}
        for provider, lanes in sorted(table._limits.items())
        for media, limit in sorted(lanes.items())
        if media in MEDIA_TYPES and limit > 0
    ]
    return {
        "defaults": _env_defaults(),
        "overrides": {m: overrides.get(m) for m in MEDIA_TYPES},
        "effective": effective,
    }


@router.get("/limits")
async def get_limits() -> dict[str, Any]:
    return await _limits_payload()


class LimitsBody(BaseModel):
    overrides: dict[str, int | None]


@router.put("/limits")
async def put_limits(body: LimitsBody, request: Request, user: CurrentUser) -> dict[str, Any]:
    unknown = set(body.overrides) - set(MEDIA_TYPES)
    if unknown or any(v is not None and v < 1 for v in body.overrides.values()):
        raise BadRequestError("queue_limit_invalid")
    before = await _read_overrides()
    async with safe_session_factory() as session:
        svc = ConfigService(session)
        for media, value in body.overrides.items():
            # 空串即清除覆盖；ConfigService 没有删除接口，读侧把非正整数一律当作「不限」。
            await svc.set_setting(GLOBAL_CAP_SETTING_KEYS[media], "" if value is None else str(value))
        await session.commit()
    after = await _read_overrides()
    await record_audit(
        "limits_changed",
        actor=user.id,
        detail={"before": before, "after": after, "at": time.time()},
    )
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()
    await notify(CHANNEL_CONTROL, {"op": "reload_config"})
    return await _limits_payload()


__all__ = ["router"]
