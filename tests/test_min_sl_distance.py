"""AC4 of #424: PERCENT_PRICE adjuster safety floor.

Covers the 2026-05-30 OCO incident root cause #3 — the adjuster was
allowed to clip SL prices to the PERCENT_PRICE boundary (e.g. +3.95%
from market) which triggers on routine 4h candle volatility, producing
the 272x APIError(-2021) flood observed in production.

The adjuster now refuses to return a stop-loss price within
`TE_MIN_SL_DISTANCE_PCT` of market (default 6.0); the dispatcher must
emit a structured rejection instead of placing such a stop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.exchange.binance import BinanceFuturesExchange


@pytest.fixture
def exchange() -> BinanceFuturesExchange:
    # Build a BinanceFuturesExchange but bypass network — only the helper
    # methods used by validate_and_adjust_price_for_percent_filter are
    # exercised, all of which are monkey-patched per test.
    exc = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
    exc.client = MagicMock()
    exc.testnet = True
    return exc


def _stub_filter(
    exchange,
    market_price: float,
    multiplier_up: float = 1.05,
    multiplier_down: float = 0.95,
) -> None:
    """Wire the helpers used by validate_and_adjust_price_for_percent_filter."""
    exchange._get_current_price = AsyncMock(return_value=market_price)
    exchange.get_percent_price_filter = MagicMock(
        return_value={
            "multiplierUp": str(multiplier_up),
            "multiplierDown": str(multiplier_down),
        }
    )


# ---------------------------------------------------------------------------
# AC4 / H4 — incident reproduction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h4_refuses_sl_inside_safety_floor_when_filter_is_tight(exchange):
    """AC4: when PERCENT_PRICE filter is ±5% and safety floor is 6%, the
    adjusted SL would be guaranteed to trigger — the adjuster MUST refuse
    instead of clipping to the boundary. Mirrors the BCHUSDT case from
    the 2026-05-30 incident (market $303.10, adjusted SL $315.07 = +3.95%)."""
    market = 303.10
    requested_sl = 442.03  # 45.84% above market — outside ±5% filter
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.05, multiplier_down=0.95
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="BCHUSDT",
        price=requested_sl,
        order_type="STOP_LOSS",
        min_safe_distance_pct=6.0,
    )

    assert is_adjusted is False
    assert adjusted_price is None
    assert "sl_unreachable_within_filter" in msg


@pytest.mark.asyncio
async def test_h4_refuses_sl_already_inside_safety_floor(exchange):
    """AC4: a stop-loss whose requested price is already inside the
    safety floor (e.g. 2% from market) MUST be refused even if it's
    within the PERCENT_PRICE filter."""
    market = 300.0
    requested_sl = 306.0  # +2% — inside the 6% floor for a short SL
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.10, multiplier_down=0.90
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="BTCUSDT",
        price=requested_sl,
        order_type="STOP_LOSS",
        min_safe_distance_pct=6.0,
    )

    assert is_adjusted is False
    assert adjusted_price is None
    assert "sl_within_safety_floor" in msg


@pytest.mark.asyncio
async def test_h4_accepts_sl_outside_safety_floor_within_wider_filter(exchange):
    """AC4: when the PERCENT_PRICE filter is wide enough (e.g. ±10%) and
    the requested SL sits outside both the safety floor AND the filter,
    the adjuster MUST clip to the safety-floor edge — not to the filter
    boundary that lies inside the unsafe band."""
    market = 1000.0
    requested_sl = 1500.0  # +50% — far outside any filter
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.10, multiplier_down=0.90
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="ETHUSDT",
        price=requested_sl,
        order_type="STOP_LOSS",
        min_safe_distance_pct=6.0,
    )

    assert is_adjusted is True
    assert adjusted_price is not None
    # Result MUST be >= 6% above market (the safety floor).
    pct_above = (adjusted_price - market) / market * 100
    assert pct_above >= 6.0, (
        f"adjusted SL {adjusted_price} is {pct_above:.2f}% above market — "
        "must be >= 6% safety floor"
    )


@pytest.mark.asyncio
async def test_h4_take_profit_not_subject_to_safety_floor(exchange):
    """AC4: TP orders don't trigger the safety floor — they can legitimately
    sit anywhere within the PERCENT_PRICE filter. A TP at +2% must be
    accepted unchanged."""
    market = 300.0
    requested_tp = 306.0  # +2%
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.10, multiplier_down=0.90
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="BTCUSDT",
        price=requested_tp,
        order_type="TAKE_PROFIT",
        min_safe_distance_pct=6.0,
    )

    assert is_adjusted is False
    assert adjusted_price == pytest.approx(306.0)


@pytest.mark.asyncio
async def test_h4_below_market_sl_inside_floor_refused(exchange):
    """AC4: same logic for LONG-position SLs (below market). A SL at -2%
    from market must be refused when the floor is 6%."""
    market = 300.0
    requested_sl = 294.0  # -2% — inside the 6% floor for a long SL
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.10, multiplier_down=0.90
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="BTCUSDT",
        price=requested_sl,
        order_type="STOP_LOSS",
        min_safe_distance_pct=6.0,
    )

    assert is_adjusted is False
    assert adjusted_price is None
    assert "sl_within_safety_floor" in msg


@pytest.mark.asyncio
async def test_h4_default_floor_comes_from_settings_when_not_passed(exchange):
    """AC4: when ``min_safe_distance_pct`` is omitted, the adjuster
    reads the default from ``Settings().te_min_sl_distance_pct`` (env
    ``TE_MIN_SL_DISTANCE_PCT``, default 6.0). Verify by exercising the
    same +2% short-SL refusal without explicit kwarg."""
    market = 300.0
    requested_sl = 306.0  # +2% — inside the default 6% floor
    _stub_filter(
        exchange, market_price=market, multiplier_up=1.10, multiplier_down=0.90
    )

    (
        is_adjusted,
        adjusted_price,
        msg,
    ) = await exchange.validate_and_adjust_price_for_percent_filter(
        symbol="BTCUSDT",
        price=requested_sl,
        order_type="STOP_LOSS",
    )

    assert is_adjusted is False
    assert adjusted_price is None
    assert "sl_within_safety_floor" in msg
