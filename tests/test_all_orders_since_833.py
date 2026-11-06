"""BinanceFuturesExchange.fetch_all_orders_since walks API limits (#833)."""

from __future__ import annotations

import time

import pytest

from tradeengine.exchange import binance as binance_module
from tradeengine.exchange.binance import BinanceFuturesExchange

DAY_MS = 24 * 60 * 60 * 1000


class _FakeClient:
    def __init__(self, orders: list[dict]) -> None:
        self.orders = orders
        self.calls: list[dict] = []

    def futures_get_all_orders(self, **params):
        self.calls.append(params)
        assert params["endTime"] - params["startTime"] < 7 * DAY_MS
        rows = [
            o
            for o in self.orders
            if o["symbol"] == params["symbol"]
            and params["startTime"] <= o["time"] <= params["endTime"]
        ]
        return rows[: params["limit"]]

    def futures_account_trades(self, **params):  # pragma: no cover - not used
        raise AssertionError("orders reader must not read trades")


def _exchange(client) -> BinanceFuturesExchange:
    exchange = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
    exchange.client = client
    return exchange


def _order(order_id: int, at_ms: int, symbol: str = "BTCUSDT") -> dict:
    return {
        "symbol": symbol,
        "orderId": order_id,
        "clientOrderId": f"cid-{order_id}",
        "time": at_ms,
    }


@pytest.mark.asyncio
async def test_orders_walk_windows_and_dedupe_by_order_id(monkeypatch) -> None:
    monkeypatch.setattr(binance_module, "_USER_TRADES_PAGE_LIMIT", 2)
    now_ms = int(time.time() * 1000)
    start = now_ms - 10 * DAY_MS
    orders = [
        _order(1, start + 1),
        _order(2, start + 2),
        _order(3, start + 3),
        _order(4, now_ms - DAY_MS),
        _order(9, start + 5, symbol="ETHUSDT"),
    ]
    client = _FakeClient(orders)

    result = await _exchange(client).fetch_all_orders_since("BTCUSDT", start)

    assert [o["orderId"] for o in result] == [1, 2, 3, 4]
    assert [o["clientOrderId"] for o in result][0] == "cid-1"
    assert all(call["symbol"] == "BTCUSDT" for call in client.calls)


@pytest.mark.asyncio
async def test_orders_beyond_retention_raise_without_calling() -> None:
    client = _FakeClient([])
    with pytest.raises(ValueError, match="retention"):
        await _exchange(client).fetch_all_orders_since(
            "BTCUSDT", int(time.time() * 1000) - 200 * DAY_MS
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_orders_missing_client_raises() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await _exchange(None).fetch_all_orders_since("BTCUSDT", 0)


@pytest.mark.asyncio
async def test_orders_unexpected_payload_raises() -> None:
    class _Bad(_FakeClient):
        def futures_get_all_orders(self, **params):
            return {"code": -1000}

    with pytest.raises(RuntimeError, match="unexpected allOrders payload"):
        await _exchange(_Bad([])).fetch_all_orders_since(
            "BTCUSDT", int(time.time() * 1000) - DAY_MS
        )
