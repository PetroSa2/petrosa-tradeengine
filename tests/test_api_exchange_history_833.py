"""Read-only fills/orders routes for data-manager's ground truth (#833)."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from tradeengine import api_exchange_history_routes as routes

FILL = {
    "id": 11,
    "orderId": 22,
    "symbol": "BTCUSDT",
    "side": "SELL",
    "positionSide": "BOTH",
    "price": "101000",
    "qty": "0.010",
    "quoteQty": "1010",
    "realizedPnl": "10.0",
    "commission": "0.404",
    "commissionAsset": "USDT",
    "maker": False,
    "time": 1_700_000_000_000,
    "buyer": False,  # not projected
}
ORDER = {
    "orderId": 22,
    "clientOrderId": "iceberg_detector-abc",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "positionSide": "BOTH",
    "type": "STOP_MARKET",
    "origType": "STOP_MARKET",
    "status": "FILLED",
    "reduceOnly": True,
    "closePosition": False,
    "origQty": "0.010",
    "executedQty": "0.010",
    "avgPrice": "101000",
    "stopPrice": "101000",
    "time": 1_699_999_000_000,
    "updateTime": 1_700_000_000_000,
    "workingType": "MARK_PRICE",  # not projected
}


class _Exchange:
    def __init__(self) -> None:
        self.fetch_user_trades_since = AsyncMock(return_value=[FILL])
        self.fetch_all_orders_since = AsyncMock(return_value=[ORDER])


@pytest.fixture
def exchange():
    fake = _Exchange()
    routes.set_exchange(fake)
    yield fake
    routes.set_exchange(None)


@pytest.mark.asyncio
async def test_fills_project_the_fill_fields(exchange) -> None:
    body = await routes.get_fills(symbol="btcusdt", start_ms=1, end_ms=2)
    exchange.fetch_user_trades_since.assert_awaited_once_with("BTCUSDT", 1, 2)
    assert body["symbol"] == "BTCUSDT" and body["count"] == 1
    row = body["rows"][0]
    assert set(row) == set(routes.FILL_FIELDS)
    assert row["realizedPnl"] == "10.0" and row["commission"] == "0.404"


@pytest.mark.asyncio
async def test_orders_project_client_order_id(exchange) -> None:
    body = await routes.get_orders(symbol="BTCUSDT", start_ms=1, end_ms=2)
    assert body["rows"] == [{k: ORDER[k] for k in routes.ORDER_FIELDS}]
    assert body["rows"][0]["clientOrderId"] == "iceberg_detector-abc"


@pytest.mark.asyncio
async def test_end_defaults_to_now(exchange) -> None:
    body = await routes.get_fills(symbol="BTCUSDT", start_ms=1, end_ms=None)
    end = exchange.fetch_user_trades_since.await_args.args[2]
    assert end == body["end_ms"] and end > 1_700_000_000_000


@pytest.mark.asyncio
async def test_beyond_retention_is_422(exchange) -> None:
    exchange.fetch_user_trades_since.side_effect = ValueError("beyond retention")
    with pytest.raises(HTTPException) as err:
        await routes.get_fills(symbol="BTCUSDT", start_ms=1, end_ms=2)
    assert err.value.status_code == 422
    assert err.value.detail["error"] == "beyond_retention"


@pytest.mark.asyncio
async def test_exchange_failure_is_502_never_empty_200(exchange) -> None:
    exchange.fetch_all_orders_since.side_effect = RuntimeError("HTTP 503")
    with pytest.raises(HTTPException) as err:
        await routes.get_orders(symbol="BTCUSDT", start_ms=1, end_ms=2)
    assert err.value.status_code == 502
    assert err.value.detail == {"error": "exchange_read_failed", "reason": "HTTP 503"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbol", "start", "end", "error"),
    [
        ("BTC/USDT", 1, 2, "invalid_symbol"),
        ("", 1, 2, "invalid_symbol"),
        ("BTCUSDT", 5, 2, "invalid_range"),
        ("BTCUSDT", -1, 2, "invalid_range"),
    ],
)
async def test_bad_input_is_422(exchange, symbol, start, end, error) -> None:
    with pytest.raises(HTTPException) as err:
        await routes.get_fills(symbol=symbol, start_ms=start, end_ms=end)
    assert err.value.status_code == 422 and err.value.detail["error"] == error
    exchange.fetch_user_trades_since.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_exchange_is_503() -> None:
    routes.set_exchange(None)
    with pytest.raises(HTTPException) as err:
        await routes.get_fills(symbol="BTCUSDT", start_ms=1, end_ms=2)
    assert err.value.status_code == 503


def test_module_cannot_place_cancel_or_amend_orders() -> None:
    """The routes only reach the two read-only history readers."""
    tree = ast.parse(inspect.getsource(routes))
    names = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    forbidden = {
        "execute",
        "create_order",
        "futures_create_order",
        "futures_cancel_order",
        "futures_cancel_all_open_orders",
        "futures_modify_order",
        "cancel_order",
        "place_order",
        "close_position",
    }
    assert not names & forbidden
    readers = {n for n in names if n.startswith("fetch_")}
    assert readers == {"fetch_user_trades_since", "fetch_all_orders_since"}
