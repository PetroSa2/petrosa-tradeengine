"""Adversarial tests for tradeengine#503 — OCO STOP path skips PERCENT_PRICE clamp.

`BinanceFuturesExchange._execute_stop_order` (STOP_MARKET, the leg used by the
OCO path) submits ``triggerPrice`` verbatim with NO call to
``validate_and_adjust_price_for_percent_filter``. The stop-LIMIT sibling
(``_execute_stop_limit_order``) DOES validate. So an out-of-band SL (e.g.
XLMUSDT 14.8% away when the filter allows ±5%) ships unmodified and Binance
rejects it with -2021, contributing to naked positions.

Correct behavior: the STOP_MARKET OCO leg must run its trigger price through
the PERCENT_PRICE validator/adjuster before submission (and refuse cleanly if
it cannot be made compliant).

We assert this at two levels:
  1. Static/structural: the validator is invoked on the STOP_MARKET path.
  2. Behavioral: an out-of-band trigger price is either adjusted into the band
     or the order is refused — never shipped as-is.

xfail(strict) → red now, flips loud when #503 lands.
"""

from __future__ import annotations

import inspect

import pytest

from contracts.order import OrderStatus, TradeOrder


def test_stop_limit_path_validates_but_stop_market_does_not_currently() -> None:
    """Documents the asymmetry: stop-LIMIT validates, stop-MARKET does not.

    This test PASSES today (documents the bug). When #503 is fixed the
    stop-market source should also reference the validator and this test's
    second assertion should be updated.
    """
    from tradeengine.exchange import binance as _b

    stop_limit_src = inspect.getsource(
        _b.BinanceFuturesExchange._execute_stop_limit_order
    )
    stop_market_src = inspect.getsource(_b.BinanceFuturesExchange._execute_stop_order)

    # stop-limit already guards its price
    assert (
        "validate_price_within_percent_filter" in stop_limit_src
        or "validate_and_adjust_price_for_percent_filter" in stop_limit_src
    )
    # stop-market currently does NOT — this is the bug.
    assert (
        "validate_and_adjust_price_for_percent_filter" not in stop_market_src
        and "validate_price_within_percent_filter" not in stop_market_src
    ), "If this fails, #503 may be fixed — flip the xfail test below to a real assert"


@pytest.mark.xfail(
    strict=True,
    reason="#503: STOP_MARKET (OCO leg) must validate triggerPrice against PERCENT_PRICE",
)
def test_stop_market_path_references_percent_filter_validator() -> None:
    """Post-fix: the STOP_MARKET path must invoke the PERCENT_PRICE validator."""
    from tradeengine.exchange import binance as _b

    src = inspect.getsource(_b.BinanceFuturesExchange._execute_stop_order)
    assert "validate_and_adjust_price_for_percent_filter" in src or (
        "validate_price_within_percent_filter" in src
    ), "OCO STOP_MARKET leg ships triggerPrice without PERCENT_PRICE validation (#503)"


class TestOutOfBandTriggerRefused:
    """Behavioral: an out-of-band SL trigger must be adjusted or refused."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="#503: out-of-band OCO stop trigger is shipped verbatim, not clamped/refused",
    )
    async def test_xlm_out_of_band_sl_is_not_shipped_verbatim(self) -> None:
        """XLMUSDT scenario: market ~0.19, SL 14.8% away, filter ±5%.

        The OCO STOP_MARKET leg must NOT submit the raw out-of-band trigger.
        Either it adjusts into the band, or it refuses — but the exact raw
        0.162 must never reach the algo-order API.
        """
        from unittest.mock import AsyncMock, MagicMock

        from tradeengine.exchange.binance import BinanceFuturesExchange

        exch = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
        exch.client = MagicMock()
        # market 0.19, filter ±5%
        exch._get_current_price = AsyncMock(return_value=0.19)  # type: ignore[method-assign]
        exch.get_percent_price_filter = MagicMock(  # type: ignore[method-assign]
            return_value={"multiplierUp": "1.05", "multiplierDown": "0.95"}
        )
        exch._format_price = MagicMock(side_effect=lambda s, p: f"{p:.5f}")  # type: ignore[method-assign]

        captured: dict = {}

        async def _fake_algo_api(**params: object) -> dict:
            captured.update(params)
            return {"algoId": "999", "algoStatus": "NEW"}

        exch._call_algo_order_api = _fake_algo_api  # type: ignore[method-assign]

        async def _retry(fn, **kw):  # type: ignore[no-untyped-def]
            return await fn(**kw)

        exch._execute_with_retry = _retry  # type: ignore[method-assign]

        order = TradeOrder(
            symbol="XLMUSDT",
            side="sell",
            type="stop",
            amount=579.0,
            stop_loss=0.16215,  # 14.8% below market 0.19 — out of ±5% band
            position_side="LONG",
            reduce_only=True,
            status=OrderStatus.PENDING,
        )

        await exch._execute_stop_order(order)

        # The raw out-of-band trigger must not have been shipped.
        shipped = str(captured.get("triggerPrice", ""))
        assert shipped not in ("0.16215", "0.16215000"), (
            f"OCO STOP shipped out-of-band trigger {shipped} verbatim (#503)"
        )
