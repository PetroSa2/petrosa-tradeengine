"""Tests for decision_id read and propagation in tradeengine (P0.1b / petrosa_k8s#579)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from contracts.signal import Signal
from shared.constants import UTC

try:
    from datetime import UTC  # noqa: F811
except ImportError:
    pass

from datetime import datetime

_RECENT_TS = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()


class TestSignalDecisionIdField:
    def test_decision_id_defaults_to_none(self):
        signal = Signal(
            strategy_id="s1",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.01,
            current_price=50000.0,
            source="test",
            strategy="s1",
            timestamp=_RECENT_TS,
        )
        assert signal.decision_id is None

    def test_decision_id_preserved_when_provided(self):
        signal = Signal(
            strategy_id="s1",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.01,
            current_price=50000.0,
            source="petrosa-cio",
            strategy="s1",
            decision_id="abcdef1234567890abcdef1234567890",
            timestamp=_RECENT_TS,
        )
        assert signal.decision_id == "abcdef1234567890abcdef1234567890"

    def test_signal_round_trip_preserves_decision_id(self):
        signal = Signal(
            strategy_id="s1",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.01,
            current_price=50000.0,
            source="petrosa-cio",
            strategy="s1",
            decision_id="did123",
            timestamp=_RECENT_TS,
        )
        data = signal.model_dump()
        signal2 = Signal(**data)
        assert signal2.decision_id == "did123"


class TestSetDecisionContextOnSpan:
    def test_decision_id_set_on_span_when_present(self, real_petrosa_otel):
        from unittest.mock import MagicMock

        from petrosa_otel import set_decision_context

        span = MagicMock()
        span.is_recording.return_value = True
        captured = {}
        span.set_attribute.side_effect = lambda k, v: captured.update({k: v})

        signal = Signal(
            strategy_id="momentum_v1",
            symbol="ETHUSDT",
            action="sell",
            confidence=0.9,
            price=3000.0,
            quantity=0.1,
            current_price=3000.0,
            source="petrosa-cio",
            strategy="momentum_v1",
            decision_id="deadbeef" * 4,
            timestamp=_RECENT_TS,
        )

        set_decision_context(
            span,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            action=signal.action,
            confidence=signal.confidence,
            **({"decision_id": signal.decision_id} if signal.decision_id else {}),
        )

        assert captured.get("decision.decision_id") == "deadbeef" * 4
        assert captured.get("decision.strategy_id") == "momentum_v1"
        assert captured.get("decision.symbol") == "ETHUSDT"

    def test_no_decision_id_key_when_none(self, real_petrosa_otel):
        from unittest.mock import MagicMock

        from petrosa_otel import set_decision_context

        span = MagicMock()
        span.is_recording.return_value = True
        captured = {}
        span.set_attribute.side_effect = lambda k, v: captured.update({k: v})

        signal = Signal(
            strategy_id="s1",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.01,
            current_price=50000.0,
            source="direct",
            strategy="s1",
            timestamp=_RECENT_TS,
        )

        set_decision_context(
            span,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            action=signal.action,
            confidence=signal.confidence,
            **({"decision_id": signal.decision_id} if signal.decision_id else {}),
        )

        assert "decision.decision_id" not in captured
        assert captured.get("decision.strategy_id") == "s1"
