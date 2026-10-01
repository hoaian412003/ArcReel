"""解析 RunningHub 查询响应里的 ``usage`` 计费字段。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunningHubUsage:
    """``consumeCoins`` 是 workflow 在 RH 机器上的运行消耗（RH coin）；
    ``thirdPartyConsumeMoney`` 是 workflow 内调用第三方模型的美元费用。"""

    coins: float | None = None
    third_party_usd: float | None = None


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_runninghub_usage(payload: object) -> RunningHubUsage:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return RunningHubUsage()
    return RunningHubUsage(
        coins=_positive_number(usage.get("consumeCoins")),
        third_party_usd=_positive_number(usage.get("thirdPartyConsumeMoney")),
    )
