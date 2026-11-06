"""Read-only account history from the exchange: fills and orders (#833).

Tradeengine holds the Binance account keys; data-manager holds none. Data-manager's
per-strategy ground truth (PetroSa2/petrosa-data-manager#844) reads the account's own
fills (``realizedPnl``, ``commission``) and orders (``clientOrderId``) through these
routes, so the keys never leave tradeengine.

Read-only by construction: the routes only call ``fetch_user_trades_since`` and
``fetch_all_orders_since`` (``GET /fapi/v1/userTrades`` and ``/fapi/v1/allOrders``).
Nothing here places, cancels or amends an order. A failed read is an explicit error,
never an empty 200: a range beyond the exchange's retention is 422, an exchange
failure 502.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exchange", tags=["exchange-history"])

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
    end = now_ms if end_ms is None else end_ms
    if start_ms < 0 or start_ms > end:
        raise HTTPException(status_code=422, detail={"error": "invalid_range"})
    return normalized, end


async def _read(
    method_name: str,
    fields: tuple[str, ...],
    symbol: str,
    start_ms: int,
    end_ms: int | None,
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
            status_code=422,
            detail={"error": "beyond_retention", "reason": str(exc)},
        ) from exc
    except Exception as exc:
        logger.warning(
            "exchange history read failed: %s %s [%s, %s]: %s",
            method_name,
            normalized,
            start_ms,
            end,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "exchange_read_failed", "reason": str(exc)},
        ) from exc
    projected = [{key: row.get(key) for key in fields} for row in rows]
    return {
        "symbol": normalized,
        "start_ms": start_ms,
        "end_ms": end,
        "count": len(projected),
        "rows": projected,
    }


@router.get("/fills")
async def get_fills(
    symbol: str = Query(..., description="Futures symbol, e.g. BTCUSDT"),
    start_ms: int = Query(..., description="Range start, epoch ms"),
    end_ms: int | None = Query(
        None, description="Range end, epoch ms (now if omitted)"
    ),
) -> dict[str, Any]:
    """The account's fills (userTrades) for ``symbol`` in the range, oldest first."""
    return await _read("fetch_user_trades_since", FILL_FIELDS, symbol, start_ms, end_ms)


@router.get("/orders")
async def get_orders(
    symbol: str = Query(..., description="Futures symbol, e.g. BTCUSDT"),
    start_ms: int = Query(..., description="Range start, epoch ms"),
    end_ms: int | None = Query(
        None, description="Range end, epoch ms (now if omitted)"
    ),
) -> dict[str, Any]:
    """The account's orders (allOrders) for ``symbol`` created in the range, oldest first."""
    return await _read("fetch_all_orders_since", ORDER_FIELDS, symbol, start_ms, end_ms)
