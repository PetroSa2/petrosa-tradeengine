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


def test_both_stop_paths_validate_percent_filter() -> None:
    """#503 (fixed): both stop-LIMIT and stop-MARKET guard the price."""
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
    # stop-market now validates too (#503).
    assert (
        "validate_and_adjust_price_for_percent_filter" in stop_market_src
        or "validate_price_within_percent_filter" in stop_market_src
    ), "OCO STOP_MARKET leg must validate triggerPrice against PERCENT_PRICE (#503)"


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

        # #541: this SL is 14.8% below market with a ±5% filter and a 6% safety
        # floor — the floor is FARTHER than the filter allows, so no price
        # satisfies both. Previously this refused (raised) and left the position
        # NAKED. It now CLAMPS to the furthest placeable price (just inside the
        # ±5% filter) and ships that — a slightly-tight stop beats no stop.
        await exch._execute_stop_order(order)

        # The raw out-of-band trigger must NEVER be shipped verbatim (#503 intent
        # preserved) — and what IS shipped must sit inside the ±5% filter (#541).
        shipped = str(captured.get("triggerPrice", ""))
        assert shipped not in ("0.16215", "0.16215000"), (
            f"OCO STOP shipped out-of-band trigger {shipped} verbatim (#503)"
        )
        shipped_val = float(captured.get("triggerPrice"))
        market = 0.19
        dev_pct = abs(shipped_val - market) / market * 100
        assert dev_pct < 5.0, (
            f"#541: shipped SL {shipped_val} ({dev_pct:.2f}%) must be clamped "
            f"inside the ±5% PERCENT_PRICE filter"
        )

    @pytest.mark.asyncio
    async def test_adjustable_out_of_band_sl_is_clamped_then_shipped(self) -> None:
        """AC1: a slightly out-of-band SL that CAN be made compliant is clamped
        into the band and shipped — not rejected. Market 100, filter ±10%, SL
        at 88 (12% below, just outside ±10%): the adjuster pulls it to the
        filter edge (still beyond the 6% safety floor), so it ships adjusted.
        """
        from unittest.mock import AsyncMock, MagicMock

        from tradeengine.exchange.binance import BinanceFuturesExchange

        exch = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
        exch.client = MagicMock()
        exch._get_current_price = AsyncMock(return_value=100.0)  # type: ignore[method-assign]
        exch.get_percent_price_filter = MagicMock(  # type: ignore[method-assign]
            return_value={"multiplierUp": "1.10", "multiplierDown": "0.90"}
        )
        exch._format_price = MagicMock(side_effect=lambda s, p: f"{p:.4f}")  # type: ignore[method-assign]

        captured: dict = {}

        async def _fake_algo_api(**params: object) -> dict:
            captured.update(params)
            return {"algoId": "1000", "algoStatus": "NEW"}

        exch._call_algo_order_api = _fake_algo_api  # type: ignore[method-assign]

        async def _retry(fn, **kw):  # type: ignore[no-untyped-def]
            return await fn(**kw)

        exch._execute_with_retry = _retry  # type: ignore[method-assign]

        order = TradeOrder(
            symbol="BTCUSDT",
            side="sell",
            type="stop",
            amount=1.0,
            stop_loss=88.0,  # 12% below market 100 — just outside ±10% band
            position_side="LONG",
            reduce_only=True,
            status=OrderStatus.PENDING,
        )

        await exch._execute_stop_order(order)

        shipped = float(captured["triggerPrice"])
        # Clamped into the ±10% band (with the adjuster's 1% safety margin),
        # so strictly between the raw 88 and market, and never the raw value.
        assert shipped != 88.0
        assert 88.0 < shipped <= 91.0
