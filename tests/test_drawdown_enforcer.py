"""Tests for ``tradeengine/risk/drawdown_enforcer.py`` (FR30, #431)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from tradeengine.risk.drawdown_enforcer import (
    DRAWDOWN_BREACH_SUBJECT_PREFIX,
    Breach,
    DrawdownBreachEmitter,
    check_and_emit,
    check_drawdown_breach,
    get_stub_envelope_value,
)

# ─── comparator ─────────────────────────────────────────────────────────────


def test_breach_returned_when_observed_strictly_exceeds_envelope() -> None:
    breach = check_drawdown_breach(
        strategy_id="momentum-v3",
        observed_drawdown_pct=8.0,
        envelope_value_pct=5.0,
    )
    assert breach is not None
    assert breach.strategy_id == "momentum-v3"
    assert breach.observed_drawdown_pct == 8.0
    assert breach.envelope_value_pct == 5.0
    assert breach.exceeded_by_pct == pytest.approx(3.0)


def test_no_breach_when_observed_equals_envelope() -> None:
    """Equality is the one-tick grace window — same convention as
    PortfolioTracker.would_breach_ceiling."""
    result = check_drawdown_breach(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        envelope_value_pct=5.0,
    )
    assert result is None


def test_no_breach_when_observed_below_envelope() -> None:
    result = check_drawdown_breach(
        strategy_id="momentum-v3",
        observed_drawdown_pct=3.0,
        envelope_value_pct=5.0,
    )
    assert result is None


def test_empty_strategy_id_returns_none() -> None:
    result = check_drawdown_breach(
        strategy_id="",
        observed_drawdown_pct=10.0,
        envelope_value_pct=5.0,
    )
    assert result is None


# ─── stub envelope source ───────────────────────────────────────────────────


def test_stub_envelope_returns_max_daily_loss_pct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradeengine.risk.drawdown_enforcer as mod

    fake_settings = MagicMock()
    fake_settings.max_daily_loss_pct = 6.5
    monkeypatch.setattr(mod, "settings", fake_settings)
    assert get_stub_envelope_value("momentum-v3") == 6.5


def test_stub_envelope_returns_zero_when_setting_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradeengine.risk.drawdown_enforcer as mod

    class _NoAttr:
        pass

    monkeypatch.setattr(mod, "settings", _NoAttr())
    assert get_stub_envelope_value("anything") == 0.0


# ─── DrawdownBreachEmitter ──────────────────────────────────────────────────


@dataclass
class _FakeNatsClient:
    published: list[tuple[str, bytes]] = field(default_factory=list)

    async def publish(self, subject: str, body: bytes) -> None:
        self.published.append((subject, body))


@dataclass
class _FakeAlertPublisher:
    nc: Any | None = None

    async def _ensure_connected(self) -> Any | None:
        return self.nc


def _make_breach(
    *,
    strategy_id: str = "momentum-v3",
    observed: float = 8.0,
    envelope: float = 5.0,
) -> Breach:
    from datetime import datetime

    from shared.constants import UTC

    return Breach(
        strategy_id=strategy_id,
        observed_drawdown_pct=observed,
        envelope_value_pct=envelope,
        exceeded_by_pct=observed - envelope,
        detected_at=datetime.now(UTC),
    )


def test_emit_publishes_to_per_strategy_subject_when_nats_available() -> None:
    nc = _FakeNatsClient()
    publisher = _FakeAlertPublisher(nc=nc)
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)
    breach = _make_breach(strategy_id="momentum-v3")
    rc = asyncio.run(emitter.emit(breach))
    assert rc is True
    assert len(nc.published) == 1
    subject, body = nc.published[0]
    assert subject == f"{DRAWDOWN_BREACH_SUBJECT_PREFIX}.momentum-v3"
    payload = json.loads(body.decode("utf-8"))
    assert payload["strategy_id"] == "momentum-v3"
    assert payload["observed_drawdown_pct"] == 8.0
    assert payload["envelope_value_pct"] == 5.0
    assert payload["exceeded_by_pct"] == pytest.approx(3.0)
    # Envelope provenance fields default NULL — #422 will populate them.
    assert payload["envelope_version"] is None
    assert payload["envelope_source"] is None


def test_emit_returns_false_when_no_alert_publisher_attached() -> None:
    emitter = DrawdownBreachEmitter(alert_publisher=None)
    rc = asyncio.run(emitter.emit(_make_breach()))
    assert rc is False


def test_emit_returns_false_when_nats_unavailable() -> None:
    publisher = _FakeAlertPublisher(nc=None)
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)
    rc = asyncio.run(emitter.emit(_make_breach()))
    assert rc is False


def test_set_alert_publisher_updates_emitter() -> None:
    emitter = DrawdownBreachEmitter()
    assert emitter._alert_publisher is None  # noqa: SLF001
    nc = _FakeNatsClient()
    emitter.set_alert_publisher(_FakeAlertPublisher(nc=nc))
    rc = asyncio.run(emitter.emit(_make_breach()))
    assert rc is True
    assert len(nc.published) == 1


# ─── check_and_emit integration point ───────────────────────────────────────


def test_check_and_emit_fires_when_breach_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher-call-site path: comparator fires AND emitter publishes."""
    nc = _FakeNatsClient()
    publisher = _FakeAlertPublisher(nc=nc)
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)
    breach = asyncio.run(
        check_and_emit(
            strategy_id="momentum-v3",
            observed_drawdown_pct=10.0,
            emitter=emitter,
            envelope_value_pct=5.0,
        )
    )
    assert breach is not None
    assert breach.exceeded_by_pct == pytest.approx(5.0)
    assert len(nc.published) == 1


def test_check_and_emit_skips_emission_when_no_breach() -> None:
    nc = _FakeNatsClient()
    publisher = _FakeAlertPublisher(nc=nc)
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)
    breach = asyncio.run(
        check_and_emit(
            strategy_id="momentum-v3",
            observed_drawdown_pct=3.0,
            emitter=emitter,
            envelope_value_pct=5.0,
        )
    )
    assert breach is None
    assert nc.published == []


def test_check_and_emit_falls_back_to_stub_envelope_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When envelope_value_pct is not supplied, get_stub_envelope_value is used."""
    import tradeengine.risk.drawdown_enforcer as mod

    fake_settings = MagicMock()
    fake_settings.max_daily_loss_pct = 4.0
    monkeypatch.setattr(mod, "settings", fake_settings)

    nc = _FakeNatsClient()
    emitter = DrawdownBreachEmitter(alert_publisher=_FakeAlertPublisher(nc=nc))
    breach = asyncio.run(
        check_and_emit(
            strategy_id="momentum-v3",
            observed_drawdown_pct=7.0,
            emitter=emitter,
            # envelope_value_pct omitted on purpose
        )
    )
    assert breach is not None
    assert breach.envelope_value_pct == 4.0
    assert breach.exceeded_by_pct == pytest.approx(3.0)
