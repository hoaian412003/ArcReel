"""JSON-safe serialization for typed Claude Agent SDK messages."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Path, Enum)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(cast(Any, value)))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json_safe(model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return {key: json_safe(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def sdk_message_payload(message: Any) -> dict[str, Any]:
    payload = json_safe(message)
    if not isinstance(payload, dict):
        payload = {"value": payload}
    payload.setdefault("message_type", type(message).__name__)
    return payload
