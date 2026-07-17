"""Adversarial tests for tradeengine#501 — MARKET fill_price collapses to 0.

`BinanceFuturesExchange._format_execution_result` (tradeengine/exchange/binance.py
:1343) computes:

    "fill_price": result.get("price") or result.get("triggerPrice")

For a Binance FUTURES MARKET order the response returns ``price = "0.00000000"``
and no ``triggerPrice`` — the real average fill lives in ``fills[]`` (or
``cumQuote``/``executedQty``). The method already computes
``total_quote_qty = sum(quoteQty)`` and ``total_qty = sum(qty)`` from ``fills``
but throws them away for ``fill_price``. Result: ``fill_price`` is 0/None, the
dispatcher falls back to the unvalidated ``signal.current_price`` as the SL/TP
reference anchor, and every protective order is computed off a possibly-stale
number.

Correct behavior: for a filled MARKET order, ``fill_price`` must equal the
volume-weighted average fill = ``total_quote_qty / total_qty``.

xfail(strict) → red now, flips loud when #501 lands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.order import OrderStatus, TradeOrder


def _market_order() -> TradeOrder:
    return TradeOrder(
        symbol="BCHUSDT",
        side="buy",
        type="market",
        amount=0.22,
        status=OrderStatus.PENDING,
        position_side="LONG",
    )


def _binance_market_response() -> dict:
    """Realistic Binance futures MARKET response: price='0', real fill in fills[].

    0.22 filled: 0.1 @ 224.00 + 0.12 @ 224.50 → quote 22.40 + 26.94 = 49.34,
    qty 0.22 → VWAP = 49.34 / 0.22 = 224.2727...
    """
    return {
        "orderId": 123456789,
        "symbol": "BCHUSDT",
        "status": "FILLED",
        "type": "MARKET",
        "side": "BUY",
        "price": "0.00000000",  # <-- the trap
        "avgPrice": "224.27272727",
        "executedQty": "0.22",
        "cumQuote": "49.34",
        "transactTime": 1784225587901,
        "fills": [
            {
                "price": "224.00",
                "qty": "0.10",
                "quoteQty": "22.40",
                "commission": "0.01",
            },
            {
                "price": "224.50",
                "qty": "0.12",
                "quoteQty": "26.94",
                "commission": "0.01",
            },
        ],
    }


def _format(result: dict, order: TradeOrder) -> dict:
    """Call the private formatter without constructing a live exchange client."""
    from tradeengine.exchange.binance import BinanceFuturesExchange

    return BinanceFuturesExchange._format_execution_result(
        BinanceFuturesExchange.__new__(BinanceFuturesExchange), result, order
    )


class TestMarketFillPrice:
    def test_formatter_extracts_fills(self) -> None:
        """Sanity: the formatter already aggregates fills into total_value/amount."""
        out = _format(_binance_market_response(), _market_order())
        assert out["amount"] == pytest.approx(0.22)
        assert out["total_value"] == pytest.approx(49.34)

    def test_market_fill_price_is_vwap_not_zero(self) -> None:
        """#501 (fixed): MARKET fill_price is the VWAP from fills, never 0."""
        out = _format(_binance_market_response(), _market_order())
        fp = out["fill_price"]
        assert fp not in (None, 0, "0", "0.00000000"), (
            "MARKET fill_price collapsed to 0 — SL/TP will anchor to stale signal price"
        )
        assert float(fp) == pytest.approx(49.34 / 0.22, rel=1e-4)  # VWAP ≈ 224.27

    def test_market_fill_price_from_cumquote_when_no_fills(self) -> None:
        """#501: fall back to cumQuote/executedQty when fills[] is absent."""
        resp = _binance_market_response()
        resp.pop("fills")
        out = _format(resp, _market_order())
        assert float(out["fill_price"]) == pytest.approx(49.34 / 0.22, rel=1e-4)

    def test_limit_fill_price_uses_price_no_regression(self) -> None:
        """AC4: LIMIT orders with a real price and no fills keep using price."""
        limit_resp = {
            "orderId": 987654321,
            "symbol": "BCHUSDT",
            "status": "NEW",
            "type": "LIMIT",
            "side": "BUY",
            "price": "220.50",
            "executedQty": "0",
            "cumQuote": "0",
            "fills": [],
        }
        out = _format(limit_resp, _market_order())
        assert float(out["fill_price"]) == pytest.approx(220.50)


def _dispatcher_with_price(live_price: float, band_up=1.05, band_down=0.95):
    """Build a bare Dispatcher whose exchange returns a fixed live price + band."""
    from tradeengine.dispatcher import Dispatcher

    d = Dispatcher()
    d.exchange = MagicMock()
    d.exchange._get_current_price = AsyncMock(return_value=live_price)
    d.exchange.get_percent_price_filter = MagicMock(
        return_value={
            "multiplierUp": str(band_up),
            "multiplierDown": str(band_down),
            "avgPriceMins": 5,
        }
    )
    return d


class TestReanchorStaleEntryPrice:
    """#501 AC2/AC3: stale-but-nonzero anchors re-anchor to live market."""

    @pytest.mark.asyncio
    async def test_stale_anchor_reanchored_to_live(self) -> None:
        # BCH market ≈ 223.66 but anchor 454 (~2x) is far outside the ±5% band.
        d = _dispatcher_with_price(223.66)
        out = await d._reanchor_entry_price_if_stale("BCHUSDT", 454.66)
        assert out == pytest.approx(223.66)

    @pytest.mark.asyncio
    async def test_in_band_anchor_preserved(self) -> None:
        # A legit fill 224.27 vs market 223.66 is inside the band — untouched.
        d = _dispatcher_with_price(223.66)
        out = await d._reanchor_entry_price_if_stale("BCHUSDT", 224.27)
        assert out == pytest.approx(224.27)

    @pytest.mark.asyncio
    async def test_live_price_failure_preserves_entry(self) -> None:
        d = _dispatcher_with_price(223.66)
        d.exchange._get_current_price = AsyncMock(side_effect=RuntimeError("boom"))
        out = await d._reanchor_entry_price_if_stale("BCHUSDT", 454.66)
        assert out == pytest.approx(454.66)
