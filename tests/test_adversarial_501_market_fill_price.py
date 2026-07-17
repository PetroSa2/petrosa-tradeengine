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

    @pytest.mark.xfail(
        strict=True,
        reason="#501: fill_price uses result['price']=0 for MARKET; must use VWAP from fills",
    )
    def test_market_fill_price_is_vwap_not_zero(self) -> None:
        out = _format(_binance_market_response(), _market_order())
        fp = out["fill_price"]
        assert fp not in (None, 0, "0", "0.00000000"), (
            "MARKET fill_price collapsed to 0 — SL/TP will anchor to stale signal price"
        )
        assert float(fp) == pytest.approx(49.34 / 0.22, rel=1e-4)  # VWAP ≈ 224.27

    def test_current_behavior_fill_price_is_zeroish(self) -> None:
        """Documents the bug as-is: fill_price is falsy for MARKET orders."""
        out = _format(_binance_market_response(), _market_order())
        fp = out["fill_price"]
        assert (fp is None) or (float(fp) == 0.0), (
            "If this fails, #501 may already be fixed — remove the xfail above"
        )
