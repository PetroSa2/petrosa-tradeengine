"""
Tests that the dispatcher correctly invokes the execution-event publisher
for both signal-keyed (rejection) and order-keyed (lifecycle) emissions.

Scope: PetroSa2/petrosa_k8s#586, P0.2c — exercises only the two emit helpers
and verifies subject/event_type/decision_id propagation. Full dispatch flow
is covered by other tests in the suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from shared.constants import UTC


def _build_signal(**overrides):
    base = {
        "strategy_id": "rsi_reversal",
        "symbol": "BTCUSDT",
        "action": "buy",
        "price": 50000.0,
        "quantity": 0.001,
        "current_price": 50000.0,
        "confidence": 0.8,
        "source": "petrosa-cio",
        "strategy": "rsi_reversal",
        "decision_id": "dec-deadbeef",
    }
    base.update(overrides)
    return Signal(**base)


def _build_order(strategy_id: str = "rsi_reversal", decision_id: str = "dec-1"):
    return TradeOrder(
        order_id="local-order-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.01,
        target_price=50000.0,
        status=OrderStatus.PENDING,
        filled_amount=0.0,
        strategy_metadata={
            "strategy_id": strategy_id,
            "decision_id": decision_id,
        },
        exchange="binance",
    )


@pytest.fixture
def dispatcher():
    """Build a minimally-initialized Dispatcher without touching network/IO."""
    from tradeengine.dispatcher import Dispatcher

    # Bypass __init__'s heavy setup by instantiating then patching just what helpers need.
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    return d


@pytest.mark.asyncio
async def test_emit_from_signal_propagates_decision_id_and_reason(dispatcher):
    signal = _build_signal(decision_id="dec-XYZ")
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._emit_execution_event_from_signal(
            signal,
            event_type="rejected",
            reason="risk_position_limit",
        )

    pub.publish.assert_awaited_once()
    kw = pub.publish.await_args.kwargs
    assert kw["event_type"] == "rejected"
    assert kw["strategy_id"] == "rsi_reversal"
    assert kw["decision_id"] == "dec-XYZ"
    assert kw["reason"] == "risk_position_limit"
    # order_id unknown pre-execution
    assert kw["order_id"] == ""
    # extra carries cheap context already on the signal
    assert kw["extra"]["symbol"] == "BTCUSDT"
    assert kw["extra"]["side"] == "buy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,reason",
    [
        ("placed", "binance_accepted"),
        ("filled", "binance_filled"),
        ("partial_fill", "partial_5_of_10"),
        ("rejected", "exchange_rejected"),
    ],
)
async def test_emit_from_order_covers_all_lifecycle_events(
    dispatcher, event_type, reason
):
    order = _build_order(strategy_id="momentum_v2", decision_id="dec-MOM")
    result = {"order_id": "binance-7777", "status": event_type, "amount": 0.005}

    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._emit_execution_event_from_order(
            order,
            result,
            event_type=event_type,
            reason=reason,
        )

    pub.publish.assert_awaited_once()
    kw = pub.publish.await_args.kwargs
    assert kw["event_type"] == event_type
    assert kw["reason"] == reason
    assert kw["strategy_id"] == "momentum_v2"
    assert kw["decision_id"] == "dec-MOM"
    # Exchange-assigned id is preferred over local fallback
    assert kw["order_id"] == "binance-7777"
    assert kw["extra"]["symbol"] == "BTCUSDT"
    assert kw["extra"]["side"] == "buy"
    assert kw["extra"]["qty"] == 0.01


@pytest.mark.asyncio
async def test_emit_from_order_falls_back_to_local_order_id(dispatcher):
    """If the exchange didn't assign an id (e.g. simulated), use the local one."""
    order = _build_order()
    result = {"order_id": None, "status": "pending"}
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._emit_execution_event_from_order(
            order, result, event_type="placed", reason="simulated_placed"
        )
    kw = pub.publish.await_args.kwargs
    assert kw["order_id"] == "local-order-1"


@pytest.mark.asyncio
async def test_emit_handles_publisher_exception_gracefully(dispatcher):
    """Publisher errors must NOT propagate up — order path stays alive."""
    signal = _build_signal()
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(side_effect=RuntimeError("nats down"))
        # Should not raise
        await dispatcher._emit_execution_event_from_signal(
            signal, event_type="rejected", reason="any"
        )
    dispatcher.logger.warning.assert_called()


@pytest.mark.asyncio
async def test_emit_strips_unknown_fields_from_signal_decision_id(dispatcher):
    """A signal with no decision_id (legacy ta-bot path) emits with None decision_id."""
    signal = _build_signal(decision_id=None)
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._emit_execution_event_from_signal(
            signal, event_type="rejected", reason="restricted_mode"
        )
    assert pub.publish.await_args.kwargs["decision_id"] is None
