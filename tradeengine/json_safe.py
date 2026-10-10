"""JSON-safe copies of write payloads (petrosa-tradeengine#755)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


def json_safe(value: Any, *, utc: bool = False) -> Any:
    """A copy of ``value`` that ``json.dumps`` accepts: containers are walked, datetimes become ISO strings,
    Decimals floats, and anything else that is not a JSON scalar becomes its string. A client object in a
    payload (the cause of #755: every entry fill failed to persist) is thus sent as text, never raised on.

    With ``utc=True`` a datetime is written as ISO-8601 in UTC (a naive one is taken as UTC, the convention of
    this service), the form the execution events carry."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v, utc=utc) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v, utc=utc) for v in value]
    if isinstance(value, datetime) and utc:
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return value.astimezone(UTC).isoformat()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)
