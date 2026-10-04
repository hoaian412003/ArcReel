"""入队准入：输入摘要、费用预估与预算闸口。

准入结果落 ``queue_audit``（接受 / 拒绝）并把摘要与预估写到任务行，之后与供应商回传的实际
用量（``api_calls.cost_amount`` / ``provider_coins``）对照。预估是尽力而为：定价缺失（自定义供应商、
未登记模型）时为 None，不据此拒绝；只有配置了预算上限且预估明确超限才拒绝入队。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import update

from lib.billing.cost_calculator import CostCalculator
from lib.billing.pricing.strategies import PricingParams
from lib.config.service import ConfigService
from lib.custom_provider import is_custom_provider
from lib.db import safe_session_factory
from lib.db.models.task import Task
from lib.infra.api_errors import UnprocessableError
from lib.infra.content_digest import canonical_json_digest

logger = logging.getLogger(__name__)

#: 单任务预估费用上限（按预估币种计，空 / 非正数 = 不限）。
SETTING_MAX_ESTIMATED_COST = "queue_max_estimated_cost_per_task"


def input_digest(payload: dict[str, Any] | None) -> str:
    """任务输入的规范化摘要；同样的输入得到同样的摘要，供审计比对。"""
    return canonical_json_digest(payload or {}, allow_nan=False)


def estimate_task_cost(
    *,
    provider_id: str | None,
    model_id: str | None,
    media_type: str,
    payload: dict[str, Any] | None,
) -> tuple[float, str] | None:
    """按定价声明预估单任务费用；任何一环缺失都返回 None（不阻断入队）。"""
    if not provider_id or media_type not in ("image", "video", "audio") or is_custom_provider(provider_id):
        # 自定义供应商的价格在库里、本函数不读库：返回 0 会被误当成「免费」，宁可不预估。
        return None
    data = payload or {}
    try:
        duration = data.get("duration_seconds")
        params = PricingParams(
            call_type=media_type,
            model=model_id or None,
            resolution=data.get("resolution"),
            aspect_ratio=data.get("aspect_ratio"),
            duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
            generate_audio=bool(data.get("generate_audio", True)),
        )
        amount, currency = CostCalculator().calculate_cost(provider_id, params, estimate_only=True)
    except Exception:
        logger.debug("费用预估不可用 provider=%s model=%s", provider_id, model_id, exc_info=True)
        return None
    return float(amount), currency


async def read_max_estimated_cost() -> float | None:
    async with safe_session_factory() as session:
        raw = await ConfigService(session).get_setting(SETTING_MAX_ESTIMATED_COST, "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


async def enforce_budget(estimate: tuple[float, str] | None) -> None:
    """预估超过配置的单任务上限时拒绝入队；未配置或无预估时放行。"""
    if estimate is None:
        return
    limit = await read_max_estimated_cost()
    if limit is not None and estimate[0] > limit:
        raise UnprocessableError(
            "queue_budget_exceeded", estimated=f"{estimate[0]:.4f}", currency=estimate[1], limit=f"{limit:.4f}"
        )


async def annotate_task_admission(task_id: str, *, digest: str, estimate: tuple[float, str] | None) -> None:
    try:
        async with safe_session_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.task_id == task_id)
                .values(
                    input_digest=digest,
                    estimated_cost_amount=estimate[0] if estimate else None,
                    estimated_currency=estimate[1] if estimate else None,
                )
            )
            await session.commit()
    except Exception:
        logger.warning("写入任务准入摘要失败 task=%s", task_id, exc_info=True)
