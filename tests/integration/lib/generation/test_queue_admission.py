"""入队准入：输入摘要、费用预估与预算闸口。"""

from __future__ import annotations

import pytest

from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.generation.queue_admission import (
    SETTING_MAX_ESTIMATED_COST,
    enforce_budget,
    estimate_task_cost,
    input_digest,
    read_max_estimated_cost,
)
from lib.infra.api_errors import UnprocessableError


def test_digest_ignores_key_order_and_changes_with_content():
    assert input_digest({"a": 1, "b": [1, 2]}) == input_digest({"b": [1, 2], "a": 1})
    assert input_digest({"a": 1}) != input_digest({"a": 2})
    assert input_digest(None) == input_digest({})


def test_estimate_is_none_when_provider_unknown_or_media_is_text():
    assert estimate_task_cost(provider_id=None, model_id=None, media_type="video", payload={}) is None
    assert estimate_task_cost(provider_id="gemini-aistudio", model_id=None, media_type="text", payload={}) is None
    assert estimate_task_cost(provider_id="custom-1", model_id="m", media_type="video", payload={}) is None


def test_estimate_prices_a_known_video_model_by_duration():
    short = estimate_task_cost(
        provider_id="gemini-aistudio", model_id=None, media_type="video", payload={"duration_seconds": 4}
    )
    long = estimate_task_cost(
        provider_id="gemini-aistudio", model_id=None, media_type="video", payload={"duration_seconds": 8}
    )

    assert short is not None
    assert long is not None
    assert short[1] == long[1] == "USD"
    assert long[0] > short[0] > 0


async def _set_limit(value: str) -> None:
    async with async_session_factory() as session:
        await ConfigService(session).set_setting(SETTING_MAX_ESTIMATED_COST, value)
        await session.commit()


@pytest.mark.asyncio
async def test_budget_is_not_enforced_until_configured():
    await _set_limit("")

    assert await read_max_estimated_cost() is None
    await enforce_budget((9999.0, "USD"))


@pytest.mark.asyncio
async def test_budget_rejects_only_estimates_above_the_limit():
    await _set_limit("1.5")
    try:
        await enforce_budget((1.5, "USD"))
        await enforce_budget(None)
        with pytest.raises(UnprocessableError) as raised:
            await enforce_budget((1.51, "USD"))
        assert raised.value.key == "queue_budget_exceeded"
    finally:
        await _set_limit("")
