"""Read-only fills/orders routes for data-manager's ground truth (#833)."""

from __future__ import annotations

import ast
import inspect
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tradeengine import api_exchange_history_routes as routes

TOKEN = "test-pair-token-not-a-real-secret"
START = 1_700_000_000_000
END = START + 60_000

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
    "time": START + 1,
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
    "time": START,
    "updateTime": START + 1,
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


async def _fills(symbol="BTCUSDT", start=START, end=END, limit=1000, after_id=None):
    return await routes.get_fills(
        symbol=symbol, start_ms=start, end_ms=end, limit=limit, after_id=after_id
    )


async def _orders(symbol="BTCUSDT", start=START, end=END, limit=1000, after_id=None):
    return await routes.get_orders(
        symbol=symbol, start_ms=start, end_ms=end, limit=limit, after_id=after_id
    )


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "path": "/api/v1/exchange/fills"})


# --- authentication: data-manager only, fail closed --------------------------------


@pytest.mark.asyncio
async def test_data_manager_with_the_pair_token_is_allowed(monkeypatch) -> None:
    monkeypatch.setenv("DM_SERVICE_TOKEN", TOKEN)
    request = _request(
        {"Authorization": f"Bearer {TOKEN}", "X-Petrosa-Service": "data-manager"}
    )
    assert await routes.require_data_manager(request) == "data-manager"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Petrosa-Service": "data-manager"},
        {"Authorization": "Bearer wrong", "X-Petrosa-Service": "data-manager"},
        {"Authorization": "Bearer ", "X-Petrosa-Service": "data-manager"},
    ],
)
async def test_unauthenticated_calls_get_401(monkeypatch, headers) -> None:
    monkeypatch.setenv("DM_SERVICE_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as err:
        await routes.require_data_manager(_request(headers))
    assert err.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["cio", "tradeengine", "", "Data-Manager"])
async def test_another_identity_gets_403(monkeypatch, service) -> None:
    monkeypatch.setenv("DM_SERVICE_TOKEN", TOKEN)
    request = _request(
        {"Authorization": f"Bearer {TOKEN}", "X-Petrosa-Service": service}
    )
    with pytest.raises(HTTPException) as err:
        await routes.require_data_manager(request)
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_no_configured_token_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("DM_SERVICE_TOKEN", raising=False)
    request = _request(
        {"Authorization": "Bearer ", "X-Petrosa-Service": "data-manager"}
    )
    with pytest.raises(HTTPException) as err:
        await routes.require_data_manager(request)
    assert err.value.status_code == 503


def test_every_route_requires_the_data_manager_identity() -> None:
    dependencies = [d.dependency for d in routes.router.dependencies]
    assert routes.require_data_manager in dependencies
    assert {r.path for r in routes.router.routes} == {
        "/api/v1/exchange/fills",
        "/api/v1/exchange/orders",
    }


# --- reads ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fills_project_the_fill_fields(exchange) -> None:
    body = await _fills(symbol="btcusdt")
    exchange.fetch_user_trades_since.assert_awaited_once_with("BTCUSDT", START, END)
    assert body["symbol"] == "BTCUSDT" and body["count"] == 1
    assert body["next_after_id"] is None
    row = body["rows"][0]
    assert set(row) == set(routes.FILL_FIELDS)
    assert row["realizedPnl"] == "10.0" and row["commission"] == "0.404"


@pytest.mark.asyncio
async def test_orders_project_client_order_id(exchange) -> None:
    body = await _orders()
    assert body["rows"] == [{k: ORDER[k] for k in routes.ORDER_FIELDS}]
    assert body["rows"][0]["clientOrderId"] == "iceberg_detector-abc"


@pytest.mark.asyncio
async def test_pages_by_id_with_next_after_id(exchange) -> None:
    exchange.fetch_user_trades_since.return_value = [
        {**FILL, "id": i} for i in (5, 3, 4, 1, 2)
    ]
    first = await _fills(limit=2)
    assert [r["id"] for r in first["rows"]] == [1, 2]
    assert first["next_after_id"] == 2
    second = await _fills(limit=2, after_id=2)
    assert [r["id"] for r in second["rows"]] == [3, 4]
    last = await _fills(limit=2, after_id=4)
    assert [r["id"] for r in last["rows"]] == [5]
    assert last["next_after_id"] is None


@pytest.mark.asyncio
async def test_range_is_bounded_to_one_exchange_span(exchange) -> None:
    with pytest.raises(HTTPException) as err:
        await _fills(end=START + routes.MAX_RANGE_MS)
    assert err.value.status_code == 422
    assert err.value.detail["error"] == "range_too_large"
    exchange.fetch_user_trades_since.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_end_is_capped_at_one_span(exchange) -> None:
    body = await _fills(start=1, end=None)
    assert body["end_ms"] == routes.MAX_RANGE_MS
    assert body["end_ms"] - body["start_ms"] < routes.MAX_RANGE_MS


@pytest.mark.asyncio
async def test_beyond_retention_is_422(exchange) -> None:
    exchange.fetch_user_trades_since.side_effect = ValueError("beyond retention")
    with pytest.raises(HTTPException) as err:
        await _fills()
    assert err.value.status_code == 422
    assert err.value.detail == {"error": "beyond_retention"}


@pytest.mark.asyncio
async def test_exchange_failure_is_502_with_the_exchange_code(exchange) -> None:
    class _ApiError(Exception):
        code = -1003
        message = "Too many requests"

    exchange.fetch_all_orders_since.side_effect = _ApiError()
    with pytest.raises(HTTPException) as err:
        await _orders()
    assert err.value.status_code == 502
    assert err.value.detail == {
        "error": "exchange_read_failed",
        "reason": "code=-1003: Too many requests",
    }


@pytest.mark.asyncio
async def test_failure_reason_and_log_never_carry_urls_or_signatures(
    exchange, caplog
) -> None:
    leaky = RuntimeError(
        "HTTPSConnectionPool: /fapi/v1/userTrades?symbol=BTCUSDT&signature=abc123secret"
    )
    exchange.fetch_user_trades_since.side_effect = leaky
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as err:
        await _fills()
    assert err.value.detail["reason"] == "RuntimeError"
    assert "signature" not in caplog.text and "abc123secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbol", "start", "end", "error"),
    [
        ("BTC/USDT", START, END, "invalid_symbol"),
        ("", START, END, "invalid_symbol"),
        ("BTCUSDT", END, START, "invalid_range"),
        ("BTCUSDT", -1, END, "invalid_range"),
    ],
)
async def test_bad_input_is_422(exchange, symbol, start, end, error) -> None:
    with pytest.raises(HTTPException) as err:
        await _fills(symbol=symbol, start=start, end=end)
    assert err.value.status_code == 422 and err.value.detail["error"] == error
    exchange.fetch_user_trades_since.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_exchange_is_503() -> None:
    routes.set_exchange(None)
    with pytest.raises(HTTPException) as err:
        await _fills()
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
