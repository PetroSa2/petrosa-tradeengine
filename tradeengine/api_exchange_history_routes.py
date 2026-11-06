"""Read-only account history from the exchange: fills and orders (#833).

Tradeengine holds the Binance account keys; data-manager holds none. Data-manager's
per-strategy ground truth (PetroSa2/petrosa-data-manager#844) reads the account's own
fills (``realizedPnl``, ``commission``) and orders (``clientOrderId``) through these
routes, so the keys never leave tradeengine.

Read-only by construction: the routes only call ``fetch_user_trades_since`` and
``fetch_all_orders_since`` (``GET /fapi/v1/userTrades`` and ``/fapi/v1/allOrders``).
Nothing here places, cancels or amends an order. A failed read is an explicit error,
never an empty 200: bad input, a too-wide range or one beyond the exchange's retention
is 422, an exchange failure 502.

Only data-manager may call them. It presents ``X-Petrosa-Service: data-manager`` and
the bearer token of the tradeengine<->data-manager service identity (secret
``petrosa-data-manager-service-identities``: tradeengine mounts it as
``DM_SERVICE_TOKEN``, data-manager holds it as the ``tradeengine`` entry of
``DM_SERVICE_TOKENS``), so no other pod holds it. Missing or wrong token: 401; a valid
token under another identity: 403; no token configured here: 503 (fails closed).

Each call covers at most one exchange query span (7 days) and returns at most
``limit`` rows, paged by id (``after_id`` / ``next_after_id``). A rate guard keeps
these reads to a fixed share of the account's per-minute request weight, so a caller
bug cannot starve trading: over it, 429 with ``Retry-After``; while the exchange
weight is near exhaustion or banned, 503. Error reasons and log
lines carry the exchange's error code and message or the exception type only, never
request URLs, signatures or keys.
"""

from __future__ import annotations

import hmac
import logging
import math
import os
import re
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

logger = logging.getLogger(__name__)

CALLER_SERVICE = "data-manager"
# One exchange query span: Binance userTrades/allOrders accept at most 7 days.
MAX_RANGE_MS = 7 * 24 * 60 * 60 * 1000
# Binance's own page size for these endpoints.
MAX_PAGE_ROWS = 1000
# Binance's documented request weight of one userTrades / allOrders call.
HISTORY_CALL_WEIGHT = 5
# Policy: history reads may use at most this share of the account's per-minute
# request weight; trading keeps the rest.
HISTORY_WEIGHT_SHARE = 0.10
# Fallback per-minute weight limit when the exchange's rate monitor is not running
# (Binance USD-M futures REQUEST_WEIGHT, 1 minute).
FALLBACK_WEIGHT_LIMIT_1M = 2400
_RATE_WINDOW_SECONDS = 60.0
_calls: dict[str, deque[float]] = {}

_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")

FILL_FIELDS = (
    "id",
    "orderId",
    "symbol",
    "side",
    "positionSide",
    "price",
    "qty",
    "quoteQty",
    "realizedPnl",
    "commission",
    "commissionAsset",
    "maker",
    "time",
)
ORDER_FIELDS = (
    "orderId",
    "clientOrderId",
    "symbol",
    "side",
    "positionSide",
    "type",
    "origType",
    "status",
    "reduceOnly",
    "closePosition",
    "origQty",
    "executedQty",
    "avgPrice",
    "stopPrice",
    "time",
    "updateTime",
)


async def require_data_manager(request: Request) -> str:
    """Authenticate the caller as data-manager; fail closed."""
    expected = os.environ.get("DM_SERVICE_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503, detail={"error": "service_auth_unconfigured"}
        )
    presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(
            status_code=401,
            detail={"error": "valid service authentication required"},
        )
    service = request.headers.get("X-Petrosa-Service", "")
    if service != CALLER_SERVICE:
        raise HTTPException(status_code=403, detail={"error": "caller_not_allowed"})
    _admit(service)
    return service


def requests_per_minute() -> int:
    """The caller's request budget, derived from the account's weight limit."""
    monitor = getattr(_exchange, "rate_monitor", None)
    limit = getattr(monitor, "limit_1m", None) or FALLBACK_WEIGHT_LIMIT_1M
    return max(1, math.floor(limit * HISTORY_WEIGHT_SHARE / HISTORY_CALL_WEIGHT))


def _admit(service: str, now: float | None = None) -> None:
    monitor = getattr(_exchange, "rate_monitor", None)
    if monitor is not None and getattr(monitor, "polling_paused", False):
        raise HTTPException(
            status_code=503,
            detail={"error": "exchange_weight_exhausted"},
            headers={"Retry-After": "60"},
        )
    clock = time.monotonic() if now is None else now
    calls = _calls.setdefault(service, deque())
    while calls and clock - calls[0] >= _RATE_WINDOW_SECONDS:
        calls.popleft()
    budget = requests_per_minute()
    if len(calls) >= budget:
        retry = max(1, math.ceil(_RATE_WINDOW_SECONDS - (clock - calls[0])))
        logger.warning(
            "exchange history rate guard: %s over %d requests/min", service, budget
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "requests_per_minute": budget},
            headers={"Retry-After": str(retry)},
        )
    calls.append(clock)


router = APIRouter(
    prefix="/api/v1/exchange",
    tags=["exchange-history"],
    dependencies=[Depends(require_data_manager)],
)

_exchange: Any = None


def set_exchange(exchange: Any) -> None:
    """Inject the exchange client (``BinanceFuturesExchange``) at app start."""
    global _exchange
    _exchange = exchange


def _validated(symbol: str, start_ms: int, end_ms: int | None) -> tuple[str, int]:
    normalized = symbol.strip().upper()
    if not _SYMBOL.match(normalized):
        raise HTTPException(status_code=422, detail={"error": "invalid_symbol"})
    now_ms = int(time.time() * 1000)
    end = min(now_ms, start_ms + MAX_RANGE_MS - 1) if end_ms is None else end_ms
    if start_ms < 0 or start_ms > end:
        raise HTTPException(status_code=422, detail={"error": "invalid_range"})
    if end - start_ms >= MAX_RANGE_MS:
        raise HTTPException(
            status_code=422,
            detail={"error": "range_too_large", "max_range_ms": MAX_RANGE_MS},
        )
    return normalized, end


def _safe_reason(exc: Exception) -> str:
    """The exchange's code and message, or the exception type: never a URL or key."""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if code is not None and isinstance(message, str):
        return f"code={code}: {message}"[:200]
    return type(exc).__name__


async def _read(
    method_name: str,
    fields: tuple[str, ...],
    id_key: str,
    symbol: str,
    start_ms: int,
    end_ms: int | None,
    limit: int,
    after_id: int | None,
) -> dict[str, Any]:
    normalized, end = _validated(symbol, start_ms, end_ms)
    reader = getattr(_exchange, method_name, None)
    if reader is None:
        raise HTTPException(
            status_code=503, detail={"error": "exchange_client_unavailable"}
        )
    try:
        rows = await reader(normalized, start_ms, end)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "beyond_retention"}
        ) from exc
    except Exception as exc:
        reason = _safe_reason(exc)
        logger.warning(
            "exchange history read failed: %s %s [%s, %s]: %s",
            method_name,
            normalized,
            start_ms,
            end,
            reason,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "exchange_read_failed", "reason": reason},
        ) from exc
    ordered = sorted(
        (
            row
            for row in rows
            if after_id is None or int(row.get(id_key) or 0) > after_id
        ),
        key=lambda row: int(row.get(id_key) or 0),
    )
    page = ordered[:limit]
    more = len(ordered) > limit
    return {
        "symbol": normalized,
        "start_ms": start_ms,
        "end_ms": end,
        "count": len(page),
        "next_after_id": int(page[-1][id_key]) if more and page else None,
        "rows": [{key: row.get(key) for key in fields} for row in page],
    }


@router.get("/fills")
async def get_fills(
    symbol: str = Query(..., description="Futures symbol, e.g. BTCUSDT"),
    start_ms: int = Query(..., description="Range start, epoch ms"),
    end_ms: int | None = Query(
        None, description="Range end, epoch ms (start + 7 days or now if omitted)"
    ),
    limit: int = Query(MAX_PAGE_ROWS, ge=1, le=MAX_PAGE_ROWS),
    after_id: int | None = Query(None, description="Only trades with id > after_id"),
) -> dict[str, Any]:
    """The account's fills (userTrades) for ``symbol`` in the range, by trade id."""
    return await _read(
        "fetch_user_trades_since",
        FILL_FIELDS,
        "id",
        symbol,
        start_ms,
        end_ms,
        limit,
        after_id,
    )


@router.get("/orders")
async def get_orders(
    symbol: str = Query(..., description="Futures symbol, e.g. BTCUSDT"),
    start_ms: int = Query(..., description="Range start, epoch ms"),
    end_ms: int | None = Query(
        None, description="Range end, epoch ms (start + 7 days or now if omitted)"
    ),
    limit: int = Query(MAX_PAGE_ROWS, ge=1, le=MAX_PAGE_ROWS),
    after_id: int | None = Query(
        None, description="Only orders with orderId > after_id"
    ),
) -> dict[str, Any]:
    """The account's orders (allOrders) for ``symbol`` created in the range."""
    return await _read(
        "fetch_all_orders_since",
        ORDER_FIELDS,
        "orderId",
        symbol,
        start_ms,
        end_ms,
        limit,
        after_id,
    )
