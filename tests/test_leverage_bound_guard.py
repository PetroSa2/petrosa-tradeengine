"""
Integration test for LeverageBoundGuard — FR64 / P6.4

AC6 scenario: CIO recommends 50x on a strategy with 10x bound →
  Trade Engine rejects → rejection_source="leverage_bound" → alert metric fires
  after breach_threshold consecutive attempts.

Also covers:
  - AC2: per-strategy bound rejection
  - AC3: portfolio-aggregate cap rejection
  - AC5: consecutive breach counting + Prometheus gauge flip
  - Happy path: within-bound order passes both checks
"""

import pytest
from prometheus_client import REGISTRY

from contracts.order import OrderStatus, TradeOrder
from tradeengine.leverage_bound_guard import LeverageBoundGuard

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order(symbol: str = "BTCUSDT", strategy_id: str = "cio_v1") -> TradeOrder:
    return TradeOrder(
        symbol=symbol,
        type="market",
        side="buy",
        amount=0.01,
        simulate=False,
        strategy_metadata={"strategy_id": strategy_id},
    )


def _config(
    leverage: int = 10,
    max_leverage_bound: int = 10,
    portfolio_leverage_cap: int = 0,
    alert_threshold: int = 3,
) -> dict:
    return {
        "leverage": leverage,
        "max_leverage_bound": max_leverage_bound,
        "portfolio_leverage_cap": portfolio_leverage_cap,
        "leverage_breach_alert_threshold": alert_threshold,
    }


# ---------------------------------------------------------------------------
# AC2: Per-strategy bound
# ---------------------------------------------------------------------------


def test_ac2_rejects_when_leverage_exceeds_bound():
    """CIO recommends 50x; strategy bound is 10x → reject."""
    guard = LeverageBoundGuard()
    order = _make_order()
    cfg = _config(leverage=50, max_leverage_bound=10)

    passed, reason = guard.check(order, cfg, open_position_leverages=[])

    assert not passed
    assert "50x" in reason
    assert "10x" in reason


def test_ac2_passes_when_leverage_equals_bound():
    guard = LeverageBoundGuard()
    order = _make_order()
    cfg = _config(leverage=10, max_leverage_bound=10)

    passed, reason = guard.check(order, cfg, open_position_leverages=[])

    assert passed
    assert reason == ""


def test_ac2_passes_when_leverage_below_bound():
    guard = LeverageBoundGuard()
    order = _make_order()
    cfg = _config(leverage=5, max_leverage_bound=10)

    passed, reason = guard.check(order, cfg, open_position_leverages=[])

    assert passed


# ---------------------------------------------------------------------------
# AC3: Portfolio-aggregate cap
# ---------------------------------------------------------------------------


def test_ac3_rejects_when_aggregate_exceeds_cap():
    """Three 10x positions already open (sum=30); new 25x would push to 55 > cap=50."""
    guard = LeverageBoundGuard()
    order = _make_order()
    cfg = _config(leverage=25, max_leverage_bound=125, portfolio_leverage_cap=50)

    passed, reason = guard.check(order, cfg, open_position_leverages=[10, 10, 10])

    assert not passed
    assert "55x" in reason
    assert "50x" in reason


def test_ac3_passes_when_aggregate_under_cap():
    """Two 10x positions open (sum=20); new 10x → 30 ≤ cap=50."""
    guard = LeverageBoundGuard()
    order = _make_order()
    cfg = _config(leverage=10, max_leverage_bound=125, portfolio_leverage_cap=50)

    passed, reason = guard.check(order, cfg, open_position_leverages=[10, 10])

    assert passed


def test_ac3_disabled_when_cap_is_zero():
    """portfolio_leverage_cap=0 means the portfolio check is disabled."""
    guard = LeverageBoundGuard()
    order = _make_order()
    # Even though aggregate would be huge, cap=0 disables the check
    cfg = _config(leverage=10, max_leverage_bound=125, portfolio_leverage_cap=0)

    passed, _ = guard.check(order, cfg, open_position_leverages=[100, 100, 100])

    assert passed


# ---------------------------------------------------------------------------
# AC5: Consecutive breach alert
# ---------------------------------------------------------------------------


def test_ac5_alert_fires_after_threshold_breaches():
    """Three consecutive rejections on same scope → alert gauge flips to 1."""
    guard = LeverageBoundGuard()
    order = _make_order(strategy_id="cio_v1")
    cfg = _config(leverage=50, max_leverage_bound=10, alert_threshold=3)

    for _ in range(3):
        passed, _ = guard.check(order, cfg, open_position_leverages=[])
        assert not passed

    scope = "cio_v1:BTCUSDT"
    assert guard._consecutive_breaches.get(scope, 0) == 3

    # Verify the Prometheus alert gauge was actually set to 1.
    gauge_value = REGISTRY.get_sample_value(
        "tradeengine_leverage_bound_breach_alert", {"scope": scope}
    )
    assert gauge_value == 1.0, f"Expected gauge=1 after threshold, got {gauge_value}"


def test_ac5_breach_counter_resets_after_pass():
    """A passing order after two breaches resets the counter."""
    guard = LeverageBoundGuard()
    order = _make_order(strategy_id="cio_v1")
    failing_cfg = _config(leverage=50, max_leverage_bound=10)
    passing_cfg = _config(leverage=5, max_leverage_bound=10)

    guard.check(order, failing_cfg, open_position_leverages=[])
    guard.check(order, failing_cfg, open_position_leverages=[])
    guard.check(order, passing_cfg, open_position_leverages=[])

    scope = "cio_v1:BTCUSDT"
    assert scope not in guard._consecutive_breaches


# ---------------------------------------------------------------------------
# AC3: Portfolio breach counter reset (M2 regression guard)
# ---------------------------------------------------------------------------


def test_ac3_portfolio_breach_counter_resets_after_pass():
    """Portfolio breach counter (portfolio:{symbol}) resets when an order passes."""
    guard = LeverageBoundGuard()
    order = _make_order(strategy_id="cio_v1")
    failing_cfg = _config(
        leverage=25, max_leverage_bound=125, portfolio_leverage_cap=50
    )
    passing_cfg = _config(
        leverage=10, max_leverage_bound=125, portfolio_leverage_cap=50
    )

    # Breach: 30 existing + 25 new = 55 > cap 50
    guard.check(order, failing_cfg, open_position_leverages=[10, 10, 10])

    portfolio_scope = f"portfolio:{order.symbol}"
    assert guard._consecutive_breaches.get(portfolio_scope, 0) == 1

    # Pass: 10 existing + 10 new = 20 ≤ cap 50 → counter must clear
    guard.check(order, passing_cfg, open_position_leverages=[10])

    assert portfolio_scope not in guard._consecutive_breaches

    gauge_value = REGISTRY.get_sample_value(
        "tradeengine_leverage_bound_breach_alert", {"scope": portfolio_scope}
    )
    assert gauge_value == 0.0, f"Expected gauge=0 after reset, got {gauge_value}"


# ---------------------------------------------------------------------------
# AC6: Full scenario — CIO 50x on 10x-bound strategy
# ---------------------------------------------------------------------------


def test_ac6_full_scenario_cio_50x_on_10x_bound_strategy():
    """
    AC6: CIO recommends 50x on a strategy with 10x bound.
    - Trade Engine rejects the order.
    - rejection_source would be set to 'leverage_bound' by the caller.
    - The guard returns (False, reason) that the dispatcher uses to call
      order.mark_rejected(source='leverage_bound', reason=...).
    """
    guard = LeverageBoundGuard()
    order = _make_order(symbol="BTCUSDT", strategy_id="aggressive_cio")

    cfg = _config(
        leverage=50,  # CIO recommends 50x
        max_leverage_bound=10,  # Operator bound is 10x
        portfolio_leverage_cap=0,
        alert_threshold=1,  # Alert immediately
    )

    passed, reason = guard.check(order, cfg, open_position_leverages=[])

    assert not passed, "Order must be rejected when leverage exceeds bound"
    assert "50x" in reason
    assert "10x" in reason

    # Simulate the dispatcher calling mark_rejected
    order.mark_rejected(source="leverage_bound", reason=reason)

    assert order.status == OrderStatus.REJECTED
    assert order.rejection_source == "leverage_bound"
    assert "50x" in (order.rejection_reason or "")

    # Alert should have fired (threshold=1 means first breach triggers it)
    scope = "aggressive_cio:BTCUSDT"
    assert guard._consecutive_breaches.get(scope, 0) >= 1
