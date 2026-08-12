"""
#529: exchange-sourced fill audit enrichment.

Binance USDⓈ-M Futures ``futures_create_order`` responses omit the per-trade
``fills[]`` array, so ``fee`` / ``fee_asset`` / ``pnl`` are unavailable when the
``execution.events`` fill event is emitted. ``_enrich_fill_audit`` backfills them
best-effort from ``GET /fapi/v1/userTrades`` (``futures_account_trades``).

These tests exercise the enrichment helper in isolation plus the
``_format_execution_result`` passthrough that surfaces ``pnl`` to the dispatcher.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, Mock

import pytest

# Mock the binance SDK before importing the exchange module (mirrors the setup in
# test_binance_exchange_comprehensive.py so this file is import-order independent).
_mock_binance = MagicMock()
_mock_binance.exceptions = MagicMock()
sys.modules.setdefault("binance", _mock_binance)
sys.modules.setdefault("binance.exceptions", _mock_binance.exceptions)
_mock_binance.enums = MagicMock()
sys.modules.setdefault("binance.enums", _mock_binance.enums)


class _MockBinanceAPIException(Exception):
    def __init__(self, response=None, message=""):
        self.response = response
        self.message = message
        self.code = -1000


_mock_binance.exceptions.BinanceAPIException = _MockBinanceAPIException

from contracts.order import OrderSide, OrderType, TradeOrder  # noqa: E402
from tradeengine.exchange.binance import BinanceFuturesExchange  # noqa: E402


def _build_order(amount: float = 0.02) -> TradeOrder:
    return TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=amount,
        target_price=61000.0,
    )


def _exchange_with_trades(trades) -> BinanceFuturesExchange:
    exchange = BinanceFuturesExchange()
    client = Mock()
    client.futures_account_trades = Mock(return_value=trades)
    exchange.client = client
    exchange.initialized = True
    return exchange


_TWO_TRADES = [
    {
        "id": 1,
        "orderId": 9999,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": "61000.0",
        "qty": "0.01",
        "quoteQty": "610.0",
        "commission": "0.244",
        "commissionAsset": "USDT",
        "realizedPnl": "0.0",
        "time": 1716163200123,
    },
    {
        "id": 2,
        "orderId": 9999,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": "61010.0",
        "qty": "0.01",
        "quoteQty": "610.1",
        "commission": "0.244",
        "commissionAsset": "USDT",
        "realizedPnl": "5.5",
        "time": 1716163200200,
    },
]


@pytest.mark.asyncio
async def test_enrich_populates_fills_and_pnl_from_user_trades():
    """A FILLED futures order with no fills[] is backfilled from userTrades."""
    exchange = _exchange_with_trades(_TWO_TRADES)
    order = _build_order()
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY"}

    await exchange._enrich_fill_audit(result, order)

    exchange.client.futures_account_trades.assert_called_once_with(
        symbol="BTCUSDT", orderId=9999
    )
    assert len(result["fills"]) == 2
    assert result["pnl"] == pytest.approx(5.5)

    # And the formatter surfaces real fee/fee_asset/pnl/fill_price to the dispatcher.
    formatted = exchange._format_execution_result(result, order)
    assert formatted["fees"] == pytest.approx(0.488)
    assert formatted["fee_asset"] == "USDT"
    assert formatted["pnl"] == pytest.approx(5.5)
    # VWAP over the two trades: (610.0 + 610.1) / 0.02
    assert formatted["fill_price"] == pytest.approx(61005.0)
    assert formatted["amount"] == pytest.approx(0.02)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["NEW", "PENDING", "CANCELED", ""])
async def test_enrich_skips_when_not_filled(status):
    """Non-fill statuses have no trades to fetch — no REST call, result untouched."""
    exchange = _exchange_with_trades(_TWO_TRADES)
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": status}

    await exchange._enrich_fill_audit(result, _build_order())

    exchange.client.futures_account_trades.assert_not_called()
    assert "fills" not in result
    assert "pnl" not in result


@pytest.mark.asyncio
async def test_enrich_skips_when_fills_already_present():
    """If the response already carried per-trade detail, don't re-fetch."""
    exchange = _exchange_with_trades(_TWO_TRADES)
    result = {
        "orderId": 9999,
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "fills": [{"price": "1", "qty": "1", "quoteQty": "1", "commission": "0"}],
    }

    await exchange._enrich_fill_audit(result, _build_order())

    exchange.client.futures_account_trades.assert_not_called()
    assert len(result["fills"]) == 1


@pytest.mark.asyncio
async def test_enrich_is_best_effort_and_swallows_client_errors():
    """A userTrades failure must not raise into the (already-executed) order path."""
    exchange = BinanceFuturesExchange()
    client = Mock()
    client.futures_account_trades = Mock(side_effect=RuntimeError("api down"))
    exchange.client = client
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "FILLED"}

    # Must not raise.
    await exchange._enrich_fill_audit(result, _build_order())

    assert "fills" not in result
    assert "pnl" not in result


@pytest.mark.asyncio
async def test_enrich_skips_non_numeric_order_id():
    """Algo order ids (non-numeric) aren't queryable via userTrades."""
    exchange = _exchange_with_trades(_TWO_TRADES)
    result = {"algoId": "abc-not-int", "symbol": "BTCUSDT", "status": "FILLED"}

    await exchange._enrich_fill_audit(result, _build_order())

    exchange.client.futures_account_trades.assert_not_called()
    assert "fills" not in result


@pytest.mark.asyncio
async def test_enrich_skips_when_client_is_none():
    """No client (uninitialized) -> no-op, no crash."""
    exchange = BinanceFuturesExchange()
    exchange.client = None
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "FILLED"}

    await exchange._enrich_fill_audit(result, _build_order())

    assert "fills" not in result


@pytest.mark.asyncio
async def test_enrich_missing_commission_does_not_break_fee_summation():
    """A trade without a commission field must not raise in _calculate_fees."""
    trades = [
        {
            "id": 7,
            "orderId": 9999,
            "symbol": "BTCUSDT",
            "price": "61000.0",
            "qty": "0.02",
            "quoteQty": "1220.0",
            # no commission / commissionAsset
            "realizedPnl": "3.0",
        }
    ]
    exchange = _exchange_with_trades(trades)
    order = _build_order()
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "FILLED"}

    await exchange._enrich_fill_audit(result, order)
    formatted = exchange._format_execution_result(result, order)

    assert formatted["fees"] == pytest.approx(0.0)
    assert formatted["fee_asset"] is None
    assert formatted["pnl"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_enrich_partially_filled_is_also_enriched():
    """PARTIALLY_FILLED orders carry trades too and must be enriched."""
    exchange = _exchange_with_trades(_TWO_TRADES[:1])
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "PARTIALLY_FILLED"}

    await exchange._enrich_fill_audit(result, _build_order())

    exchange.client.futures_account_trades.assert_called_once()
    assert len(result["fills"]) == 1


@pytest.mark.asyncio
async def test_enrich_empty_user_trades_leaves_result_untouched():
    """An empty userTrades response is a no-op (no synthetic fills, no pnl)."""
    exchange = _exchange_with_trades([])
    result = {"orderId": 9999, "symbol": "BTCUSDT", "status": "FILLED"}

    await exchange._enrich_fill_audit(result, _build_order())

    assert "fills" not in result
    assert "pnl" not in result


def test_format_execution_result_exposes_pnl_key():
    """_format_execution_result must surface pnl (None when unknown)."""
    exchange = BinanceFuturesExchange()
    order = _build_order()

    without_pnl = exchange._format_execution_result(
        {"orderId": 1, "status": "FILLED", "fills": []}, order
    )
    assert "pnl" in without_pnl
    assert without_pnl["pnl"] is None

    with_pnl = exchange._format_execution_result(
        {"orderId": 1, "status": "FILLED", "fills": [], "pnl": 9.75}, order
    )
    assert with_pnl["pnl"] == pytest.approx(9.75)
