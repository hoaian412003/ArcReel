"""队列监控指标：Prometheus 文本格式，抓取时从库里现算。

指标来自表而不是进程内计数器，所以后端、队列服务任何一方重启都不会让曲线断掉或归零；
队列服务进程额外补充它独有的信息（容量上限、全局上限、本进程在跑数）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select

from lib.db import safe_session_factory
from lib.db.base import utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.generation.queue_reconciler import read_sync_stale_seconds

if TYPE_CHECKING:
    from lib.generation.generation_worker import GenerationWorker


def _esc(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._declared: set[str] = set()

    def metric(self, name: str, kind: str, help_text: str, value: float, **labels: object) -> None:
        if name not in self._declared:
            self._declared.add(name)
            self.lines.append(f"# HELP {name} {help_text}")
            self.lines.append(f"# TYPE {name} {kind}")
        label_text = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        self.lines.append(f"{name}{{{label_text}}} {value}" if label_text else f"{name} {value}")


async def render_metrics(worker: GenerationWorker | None = None) -> str:
    out = _Out()
    now = utc_now()
    stale_seconds = await read_sync_stale_seconds()
    stale_cutoff = now - timedelta(seconds=stale_seconds)
    day_ago = now - timedelta(hours=24)

    async with safe_session_factory() as session:
        by_status = (
            await session.execute(
                select(Task.status, Task.media_type, func.count()).group_by(Task.status, Task.media_type)
            )
        ).all()
        lanes = (
            await session.execute(
                select(Task.provider_id, Task.media_type, Task.status, func.count())
                .where(Task.status.in_(("queued", "running")))
                .group_by(Task.provider_id, Task.media_type, Task.status)
            )
        ).all()
        oldest = (
            await session.execute(
                select(Task.media_type, func.min(Task.queued_at))
                .where(Task.status == "queued")
                .group_by(Task.media_type)
            )
        ).all()
        stale_count = (
            await session.execute(
                select(func.count()).where(
                    Task.status == "running",
                    func.coalesce(Task.last_synced_at, Task.started_at) < stale_cutoff,
                )
            )
        ).scalar_one()
        durations = (
            await session.execute(
                select(
                    Task.media_type,
                    func.count(),
                    func.sum(func.extract("epoch", Task.finished_at) - func.extract("epoch", Task.started_at)),
                )
                .where(Task.status == "succeeded", Task.finished_at >= day_ago, Task.started_at.is_not(None))
                .group_by(Task.media_type)
            )
        ).all()
        spend = (
            await session.execute(
                select(
                    ApiCall.currency,
                    func.coalesce(func.sum(ApiCall.cost_amount), 0.0),
                    func.coalesce(func.sum(ApiCall.provider_coins), 0.0),
                    func.sum(case((ApiCall.status == "failed", 1), else_=0)),
                    func.count(),
                )
                .where(ApiCall.started_at >= day_ago)
                .group_by(ApiCall.currency)
            )
        ).all()

    for status, media, count in by_status:
        out.metric(
            "arcreel_queue_tasks", "gauge", "Tasks by status and media type", count, status=status, media_type=media
        )

    queued_by_lane: dict[tuple[str, str], int] = defaultdict(int)
    running_by_lane: dict[tuple[str, str], int] = defaultdict(int)
    for provider, media, status, count in lanes:
        key = (provider or "unknown", media)
        (queued_by_lane if status == "queued" else running_by_lane)[key] += count
    for (provider, media), count in queued_by_lane.items():
        out.metric(
            "arcreel_queue_lane_queued", "gauge", "Queued tasks per lane", count, provider=provider, media_type=media
        )
    for (provider, media), count in running_by_lane.items():
        out.metric(
            "arcreel_queue_lane_running", "gauge", "Running tasks per lane", count, provider=provider, media_type=media
        )

    for media, first in oldest:
        out.metric(
            "arcreel_queue_oldest_queued_age_seconds",
            "gauge",
            "Age of the oldest queued task",
            max(0.0, (now - (first if first.tzinfo else first.replace(tzinfo=UTC))).total_seconds()),
            media_type=media,
        )
    out.metric(
        "arcreel_queue_stale_running",
        "gauge",
        "Running tasks with no provider sync inside the staleness window",
        stale_count,
    )
    for media, count, total in durations:
        out.metric(
            "arcreel_task_duration_seconds_count", "gauge", "Succeeded tasks in the last 24h", count, media_type=media
        )
        out.metric(
            "arcreel_task_duration_seconds_sum",
            "gauge",
            "Total run seconds of succeeded tasks in 24h",
            float(total or 0.0),
            media_type=media,
        )
    for currency, cost, coins, failed, calls in spend:
        out.metric("arcreel_cost_24h", "gauge", "Provider cost in the last 24h", float(cost), currency=currency)
        out.metric(
            "arcreel_provider_coins_24h",
            "gauge",
            "RunningHub coins consumed in the last 24h",
            float(coins),
            currency=currency,
        )
        out.metric("arcreel_api_calls_24h", "gauge", "Provider API calls in the last 24h", calls, currency=currency)
        out.metric(
            "arcreel_api_calls_failed_24h",
            "gauge",
            "Failed provider API calls in the last 24h",
            int(failed or 0),
            currency=currency,
        )

    if worker is not None:
        out.metric("arcreel_queue_worker_up", "gauge", "Queue worker process is running", 1)
        out.metric(
            "arcreel_queue_worker_has_lease", "gauge", "Worker holds the queue lease", 1 if worker._owns_lease else 0
        )
        for media in ("image", "video", "audio", "text"):
            out.metric(
                "arcreel_queue_worker_inflight",
                "gauge",
                "Tasks in flight in this worker",
                worker._slots.occupied_total(media),
                media_type=media,
            )
            cap = worker.global_cap(media)
            if cap is not None:
                out.metric("arcreel_queue_global_cap", "gauge", "Global concurrency ceiling", cap, media_type=media)
        for provider, limits in worker._capacity._limits.items():
            for media, limit in limits.items():
                out.metric(
                    "arcreel_queue_lane_limit",
                    "gauge",
                    "Concurrency limit per lane",
                    limit,
                    provider=provider,
                    media_type=media,
                )
    return "\n".join(out.lines) + "\n"
