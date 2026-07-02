"""Unit tests for the #481 AC5 open/close thrash circuit-breaker."""

from __future__ import annotations

from datetime import datetime, timedelta

from shared.constants import UTC
from tradeengine.thrash_guard import ThrashCircuitBreaker


def _now() -> datetime:
    return datetime(2026, 6, 18, 19, 25, 0, tzinfo=UTC)


def test_under_cap_is_allowed() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    now = _now()
    # No events yet -> not blocked.
    assert br.should_block("BNBUSDT", now=now) is False
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    # One un-audited close, cap is 2 -> still allowed.
    assert br.should_block("BNBUSDT", now=now) is False


def test_at_cap_blocks() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    now = _now()
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    # Two un-audited closes within the window == cap -> block.
    assert br.should_block("BNBUSDT", now=now) is True


def test_audited_closes_never_count_or_block() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    now = _now()
    for _ in range(5):
        br.record_close("BNBUSDT", cio_audited=True, now=now)
    # Audited closes do not accumulate toward the threshold.
    assert br.should_block("BNBUSDT", now=now) is False


def test_window_prunes_old_events() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    start = _now()
    br.record_close("BNBUSDT", cio_audited=False, now=start)
    br.record_close("BNBUSDT", cio_audited=False, now=start)
    assert br.should_block("BNBUSDT", now=start) is True
    # 11 minutes later both events fall outside the 10-minute window.
    later = start + timedelta(minutes=11)
    assert br.should_block("BNBUSDT", now=later) is False


def test_per_symbol_isolation() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    now = _now()
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    assert br.should_block("BNBUSDT", now=now) is True
    # LINKUSDT is tracked independently.
    assert br.should_block("LINKUSDT", now=now) is False


def test_reset_clears_symbol() -> None:
    br = ThrashCircuitBreaker(max_cycles=2, window_minutes=10)
    now = _now()
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    br.record_close("BNBUSDT", cio_audited=False, now=now)
    assert br.should_block("BNBUSDT", now=now) is True
    br.reset("BNBUSDT")
    assert br.should_block("BNBUSDT", now=now) is False


def test_defaults_match_ticket() -> None:
    br = ThrashCircuitBreaker()
    assert br.max_cycles == 2
    assert br.window_minutes == 10


def test_degenerate_config_is_clamped() -> None:
    # Zero/negative inputs are clamped to a sane minimum rather than
    # disabling the breaker entirely.
    br = ThrashCircuitBreaker(max_cycles=0, window_minutes=0)
    assert br.max_cycles == 1
    assert br.window_minutes >= 1
