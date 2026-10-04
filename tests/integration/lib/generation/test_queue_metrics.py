"""监控指标：从表里现算，输出合法的 Prometheus 文本。"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from lib.db import async_session_factory
from lib.db.models.task import Task
from lib.generation.queue_metrics import render_metrics

_SAMPLE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?[0-9.e+]+$")


async def _seed(
    status: str, *, synced_ago: timedelta | None = None, started_ago: timedelta = timedelta(seconds=5)
) -> None:
    now = datetime.now(UTC)
    task_id = f"mt-{uuid.uuid4().hex[:10]}"
    async with async_session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name="metrics-demo",
                task_type="video",
                media_type="video",
                resource_id=task_id,
                status=status,
                provider_id="metrics-provider",
                queued_at=now - started_ago,
                started_at=None if status == "queued" else now - started_ago,
                last_synced_at=None if synced_ago is None else now - synced_ago,
                updated_at=now,
            )
        )
        await session.commit()


def _value(text: str, series: str) -> float:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"missing series {series}")


async def test_lane_and_staleness_series_reflect_the_table():
    await _seed("queued", started_ago=timedelta(minutes=10))
    await _seed("running", synced_ago=timedelta(hours=3), started_ago=timedelta(hours=4))

    text = await render_metrics()

    assert _value(text, 'arcreel_queue_lane_queued{provider="metrics-provider",media_type="video"}') >= 1
    assert _value(text, 'arcreel_queue_lane_running{provider="metrics-provider",media_type="video"}') >= 1
    assert _value(text, "arcreel_queue_stale_running") >= 1
    assert _value(text, 'arcreel_queue_oldest_queued_age_seconds{media_type="video"}') >= 600


async def test_output_is_well_formed_exposition_text():
    text = await render_metrics()

    for line in text.strip().splitlines():
        assert line.startswith(("# HELP ", "# TYPE ")) or _SAMPLE.match(line), line
    assert "arcreel_queue_worker_up" not in text  # 无 worker 句柄时不谎称在线
