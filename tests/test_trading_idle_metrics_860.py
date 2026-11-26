"""petrosa-tradeengine#860: idle metrics are computed at scrape time and exist from the start."""

from __future__ import annotations

from prometheus_client import REGISTRY

from tradeengine.metrics import IDLE_CAUSES, TradingIdleTracker


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _gauge(name: str, cause: str) -> float | None:
    return REGISTRY.get_sample_value(name, {"cause": cause})


def test_every_cause_has_both_series_before_any_tick():
    TradingIdleTracker(clock=Clock())
    for cause in IDLE_CAUSES:
        assert _gauge("tradeengine_trading_idle_seconds", cause) is not None
        assert _gauge("tradeengine_trading_idle_seconds_total", cause) is not None


def test_a_quiet_period_shows_at_scrape_without_observe():
    """Nothing reaches the engine (no request, no rejection): the idle run still grows."""
    clock = Clock()
    TradingIdleTracker(clock=clock)
    clock.now += 3600
    assert _gauge("tradeengine_trading_idle_seconds", "no_signals") == 3600
    assert _gauge("tradeengine_trading_idle_seconds_total", "no_signals") == 3600
    for cause in IDLE_CAUSES:
        if cause != "no_signals":
            assert _gauge("tradeengine_trading_idle_seconds_total", cause) == 0


def test_tick_attributes_the_run_to_the_previous_cause_and_switches():
    clock = Clock()
    tracker = TradingIdleTracker(clock=clock)
    clock.now += 100
    tracker.tick("cio_skip")
    clock.now += 50
    assert _gauge("tradeengine_trading_idle_seconds_total", "no_signals") == 100
    assert _gauge("tradeengine_trading_idle_seconds", "cio_skip") == 50
    assert _gauge("tradeengine_trading_idle_seconds_total", "cio_skip") == 50
    assert _gauge("tradeengine_trading_idle_seconds", "no_signals") == 0
    tracker.tick("not-a-cause")
    assert tracker.cause == "other"


def test_order_submitted_ends_the_run_and_keeps_the_total():
    clock = Clock()
    tracker = TradingIdleTracker(clock=clock)
    tracker.tick("insufficient_margin")
    clock.now += 30
    tracker.order_submitted()
    assert _gauge("tradeengine_trading_idle_seconds", "insufficient_margin") == 0
    assert _gauge("tradeengine_trading_idle_seconds_total", "insufficient_margin") == 30
    clock.now += 10
    # The blocking cause is cleared: time after the order is "no signals", not margin.
    assert _gauge("tradeengine_trading_idle_seconds", "insufficient_margin") == 0
    assert _gauge("tradeengine_trading_idle_seconds_total", "insufficient_margin") == 30
    assert _gauge("tradeengine_trading_idle_seconds", "no_signals") == 10


def test_healthy_trading_never_accrues_blocking_causes():
    """Orders keep going out: the alert's sum over blocking causes stays 0 (review of #861)."""
    clock = Clock()
    tracker = TradingIdleTracker(clock=clock)
    for _ in range(24):
        clock.now += 3600
        tracker.order_submitted()
    blocking = [c for c in IDLE_CAUSES if c not in ("no_signals", "cio_skip")]
    assert (
        sum(_gauge("tradeengine_trading_idle_seconds_total", c) for c in blocking) == 0
    )
