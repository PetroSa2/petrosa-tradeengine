"""BinanceFuturesExchange.fetch_user_trades_since walks API limits (#831)."""

from __future__ import annotations

import time

import pytest

from tradeengine.exchange import binance as binance_module
from tradeengine.exchange.binance import BinanceFuturesExchange

DAY_MS = 24 * 60 * 60 * 1000


class _FakeClient:
    """Serves userTrades like Binance: span <= 7 days, at most ``limit`` rows."""

    def __init__(self, trades: list[dict]) -> None:
        self.trades = trades
        self.calls: list[dict] = []

    def futures_account_trades(self, **params):
        self.calls.append(params)
        assert params["endTime"] - params["startTime"] < 7 * DAY_MS
        rows = [
            t
            for t in self.trades
            if t["symbol"] == params["symbol"]
            and params["startTime"] <= t["time"] <= params["endTime"]
        ]
        return rows[: params["limit"]]


def _exchange(client) -> BinanceFuturesExchange:
    exchange = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
    exchange.client = client
    return exchange


def _trade(trade_id: int, at_ms: int, symbol: str = "BTCUSDT") -> dict:
    return {"symbol": symbol, "id": trade_id, "time": at_ms, "qty": "0.001"}


@pytest.mark.asyncio
async def test_walks_seven_day_windows_and_returns_sorted_unique_trades() -> None:
    now_ms = int(time.time() * 1000)
    start = now_ms - 20 * DAY_MS
    trades = [
        _trade(3, now_ms - 1 * DAY_MS),
        _trade(1, start + 1),
        _trade(2, start + 9 * DAY_MS),
        _trade(9, start + 2 * DAY_MS, symbol="ETHUSDT"),
    ]
    client = _FakeClient(trades)

    result = await _exchange(client).fetch_user_trades_since("BTCUSDT", start)

    assert [t["id"] for t in result] == [1, 2, 3]
    assert len(client.calls) == 3  # 20 days -> three 7-day windows
    assert client.calls[0]["startTime"] == start
    assert all(call["limit"] == 1000 for call in client.calls)


@pytest.mark.asyncio
async def test_full_page_continues_from_last_trade_time(monkeypatch) -> None:
    monkeypatch.setattr(binance_module, "_USER_TRADES_PAGE_LIMIT", 2)
    now_ms = int(time.time() * 1000)
    start = now_ms - DAY_MS
    trades = [_trade(i, start + i * 1000) for i in range(1, 6)]
    client = _FakeClient(trades)

    result = await _exchange(client).fetch_user_trades_since("BTCUSDT", start)

    assert [t["id"] for t in result] == [1, 2, 3, 4, 5]
    assert len(client.calls) >= 3


@pytest.mark.asyncio
async def test_page_that_cannot_advance_raises(monkeypatch) -> None:
    monkeypatch.setattr(binance_module, "_USER_TRADES_PAGE_LIMIT", 2)
    now_ms = int(time.time() * 1000)
    start = now_ms - DAY_MS
    same_ms = [_trade(i, start) for i in range(1, 4)]

    with pytest.raises(RuntimeError, match="cannot advance"):
        await _exchange(_FakeClient(same_ms)).fetch_user_trades_since("BTCUSDT", start)


@pytest.mark.asyncio
async def test_range_beyond_retention_raises() -> None:
    start = int(time.time() * 1000) - 200 * DAY_MS
    client = _FakeClient([])

    with pytest.raises(ValueError, match="retention"):
        await _exchange(client).fetch_user_trades_since("BTCUSDT", start)
    assert client.calls == []


@pytest.mark.asyncio
async def test_missing_client_raises() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await _exchange(None).fetch_user_trades_since("BTCUSDT", 0)
