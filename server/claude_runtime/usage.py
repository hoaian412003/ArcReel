"""Turn-local token and cost deltas derived from cumulative SDK results."""

from __future__ import annotations

from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def usage_delta(
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> dict[str, int | None]:
    """Subtract cumulative counters, treating a lower value as an SDK counter reset."""
    current = current or {}
    previous = previous or {}
    result: dict[str, int | None] = {}
    for field in TOKEN_FIELDS:
        current_value = _non_negative_int(current.get(field))
        previous_value = _non_negative_int(previous.get(field))
        if current_value is None:
            result[field] = None
        elif previous_value is None or current_value < previous_value:
            result[field] = current_value
        else:
            result[field] = current_value - previous_value
    return result


def cost_delta(current: float | None, previous: float | None) -> float | None:
    if current is None:
        return None
    if previous is None or current < previous:
        return max(0.0, current)
    return max(0.0, current - previous)
