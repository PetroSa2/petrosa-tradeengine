"""
Unit tests for ExecutionEventPublisher (PetroSa2/petrosa_k8s#586, P0.2c).

Covers:
- subject construction with strategy_id
- payload schema (required keys + types)
- decision_id propagation from inbound signal
- all four lifecycle event_types: placed, filled, partial_fill, rejected
- NATS-disabled mode (publisher is no-op-safe)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.execution_event import ExecutionEvent
from tradeengine.services.execution_event_publisher import (
    ExecutionEventPublisher,
)


@pytest.fixture
def publisher():
    return ExecutionEventPublisher()


@pytest.fixture
def fake_nats_client():
    """A NATS client whose publish() records calls."""
    client = MagicMock()
    client.is_connected = True
    client.publish = AsyncMock()
    return client


# ---------- subject construction ----------


def test_build_subject_appends_strategy_id():
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_topic_execution_events = "execution.events"
        subj = ExecutionEventPublisher._build_subject("rsi_reversal")
    assert subj == "execution.events.rsi_reversal"


def test_build_subject_strips_wildcard_suffix():
    # If operator misconfigures the env with a subscription pattern.
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_topic_execution_events = "execution.events.>"
        subj = ExecutionEventPublisher._build_subject("macd_cross")
    assert subj == "execution.events.macd_cross"


def test_build_subject_unknown_strategy_id_falls_back():
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_topic_execution_events = "execution.events"
        subj = ExecutionEventPublisher._build_subject("")
    assert subj == "execution.events.unknown"


# ---------- payload schema ----------


def test_build_payload_has_all_required_fields():
    payload = ExecutionEventPublisher._build_payload(
        decision_id="dec-abc",
        strategy_id="rsi_reversal",
        order_id="ord-123",
        event_type="placed",
        reason="binance_accepted",
    )
    for required in (
        "decision_id",
        "strategy_id",
        "order_id",
        "event_type",
        "timestamp",
        "reason",
    ):
        assert required in payload, f"missing {required}"
    assert payload["decision_id"] == "dec-abc"
    assert payload["strategy_id"] == "rsi_reversal"
    assert payload["order_id"] == "ord-123"
    assert payload["event_type"] == "placed"
    assert payload["reason"] == "binance_accepted"
    # ISO-8601 string
    assert isinstance(payload["timestamp"], str)
    assert "T" in payload["timestamp"]


def test_build_payload_merges_extra_without_clobbering():
    payload = ExecutionEventPublisher._build_payload(
        decision_id="dec-xyz",
        strategy_id="s1",
        order_id="o1",
        event_type="filled",
        reason="binance_filled",
        extra={
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.01,
            # Should NOT overwrite the required event_type:
            "event_type": "rejected",
        },
    )
    assert payload["event_type"] == "filled"  # not clobbered
    assert payload["symbol"] == "BTCUSDT"
    assert payload["side"] == "buy"
    assert payload["qty"] == 0.01


def test_position_closed_payload_always_carries_row_client_order_id():
    payload = ExecutionEventPublisher._build_payload(
        decision_id="dec-close",
        strategy_id="strategy-a",
        order_id="exit-1",
        event_type="position_closed",
        reason="take_profit",
        client_order_id="cio-position-1",
        extra={
            "position_id": "row-1",
            "entry_order_id": "entry-1",
            "closed_quantity": 1.0,
            "remaining_quantity": 0.0,
            "exit_price": 120.0,
            "exit_time": "2026-10-09T16:00:00+00:00",
            "exit_order_id": "exit-1",
            "pnl_basis": "fifo_attributed",
            "pnl": 20.0,
        },
    )
    assert payload["client_order_id"] == "cio-position-1"
    assert payload["pnl_basis"] == "fifo_attributed"
    assert payload["closed_quantity"] == 1.0


def test_position_closed_contract_accepts_required_row_fields():
    event = ExecutionEvent(
        strategy_id="strategy-a",
        client_order_id="cio-position-1",
        order_id="exit-1",
        event_type="position_closed",
        timestamp="2026-10-09T16:00:00Z",
        reason="take_profit",
        position_id="row-1",
        entry_order_id="entry-1",
        closed_quantity=1.0,
        remaining_quantity=0.0,
        exit_price=120.0,
        exit_time="2026-10-09T16:00:00Z",
        exit_order_id="exit-1",
        pnl_basis="fifo_attributed",
        pnl=20.0,
        fee=-0.1,
    )
    assert event.client_order_id == "cio-position-1"
    assert event.pnl_basis == "fifo_attributed"


# ---------- publish behaviour ----------


@pytest.mark.asyncio
async def test_publish_emits_to_correct_subject(publisher, fake_nats_client):
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        ok = await publisher.publish(
            event_type="placed",
            strategy_id="rsi_reversal",
            order_id="ord-9",
            reason="binance_accepted",
            decision_id="dec-1",
        )

    assert ok is True
    assert fake_nats_client.publish.await_count == 1
    args, _ = fake_nats_client.publish.call_args
    subject, encoded = args
    assert subject == "execution.events.rsi_reversal"
    body = json.loads(encoded.decode())
    assert body["event_type"] == "placed"
    assert body["decision_id"] == "dec-1"
    assert body["order_id"] == "ord-9"
    assert body["strategy_id"] == "rsi_reversal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,reason",
    [
        ("placed", "binance_accepted"),
        ("filled", "binance_filled"),
        ("partial_fill", "partial_5_of_10"),
        ("rejected", "risk_position_limit"),
    ],
)
async def test_publish_all_four_event_types(
    publisher, fake_nats_client, event_type, reason
):
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        ok = await publisher.publish(
            event_type=event_type,
            strategy_id="strat",
            order_id="ord-1",
            reason=reason,
            decision_id="dec-1",
        )
    assert ok is True
    body = json.loads(fake_nats_client.publish.call_args[0][1].decode())
    assert body["event_type"] == event_type
    assert body["reason"] == reason


@pytest.mark.asyncio
async def test_publish_rejects_unknown_event_type(publisher, fake_nats_client):
    publisher.set_client(fake_nats_client)
    ok = await publisher.publish(
        event_type="liquidated",  # type: ignore[arg-type]
        strategy_id="strat",
        order_id="o",
        reason="nope",
    )
    assert ok is False
    fake_nats_client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_propagates_decision_id_to_payload(publisher, fake_nats_client):
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        await publisher.publish(
            event_type="filled",
            strategy_id="momentum_v2",
            order_id="exch-7777",
            reason="binance_filled",
            decision_id="decision-uuid-deadbeef",
        )
    body = json.loads(fake_nats_client.publish.call_args[0][1].decode())
    assert body["decision_id"] == "decision-uuid-deadbeef"


@pytest.mark.asyncio
async def test_publish_noop_when_nats_disabled(publisher, fake_nats_client):
    """When NATS is disabled, publisher returns False without raising."""
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = False
        s.nats_servers = None
        s.nats_topic_execution_events = "execution.events"
        # Don't inject a client — let _ensure_connected short-circuit.
        ok = await publisher.publish(
            event_type="placed",
            strategy_id="strat",
            order_id="o1",
            reason="binance_accepted",
            decision_id="d1",
        )
    assert ok is False
    fake_nats_client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_swallows_nats_errors(publisher):
    """A broken NATS publish must not raise — order path keeps going."""
    bad_client = MagicMock()
    bad_client.is_connected = True
    bad_client.publish = AsyncMock(side_effect=RuntimeError("conn closed"))
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(bad_client)
        ok = await publisher.publish(
            event_type="rejected",
            strategy_id="s",
            order_id="o",
            reason="risk_x",
            decision_id="d",
        )
    assert ok is False  # signalled, but no exception raised


# ---------- JSON-safe payloads (petrosa-tradeengine#780) ----------


def _position_closed_kwargs():
    """What position_manager and strategy_position_manager pass: real datetimes and Decimals."""
    return {
        "event_type": "position_closed",
        "strategy_id": "rsi_extreme_reversal",
        "order_id": "exit-1",
        "reason": "take_profit",
        "decision_id": "dec-close",
        "timestamp": datetime(2026, 10, 9, 16, 0, tzinfo=UTC),
        "client_order_id": "cio-position-1",
        "idempotency_key": "position_closed:exit-1:cio-position-1",
        "extra": {
            "position_id": "row-1",
            "strategy_position_id": "sp-1",
            "entry_order_id": "entry-1",
            "closed_quantity": Decimal("0.00100000"),
            "remaining_quantity": Decimal("0"),
            "exit_price": Decimal("120.5"),
            "exit_time": datetime(2026, 10, 9, 16, 0, 5, tzinfo=UTC),
            "reason": "take_profit",
            "exit_order_id": "exit-1",
            "pnl_basis": "fifo_attributed",
            "pnl": Decimal("20.25"),
            "fee": None,
        },
    }


@pytest.mark.asyncio
async def test_position_closed_with_datetime_and_decimal_fields_publishes(
    publisher, fake_nats_client
):
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        ok = await publisher.publish(**_position_closed_kwargs())

    assert ok is True
    subject, encoded = fake_nats_client.publish.call_args.args
    assert subject == "execution.events.rsi_extreme_reversal"
    body = json.loads(encoded.decode())
    assert body["exit_time"] == "2026-10-09T16:00:05+00:00"
    assert body["timestamp"] == "2026-10-09T16:00:00+00:00"
    assert body["closed_quantity"] == 0.001
    assert body["pnl"] == 20.25
    assert body["fee"] is None  # position_closed keeps its null fields


@pytest.mark.asyncio
async def test_position_closed_json_round_trips_into_the_contract(
    publisher, fake_nats_client
):
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        await publisher.publish(**_position_closed_kwargs())

    body = json.loads(fake_nats_client.publish.call_args.args[1].decode())
    event = ExecutionEvent(**body)
    assert event.event_type == "position_closed"
    assert event.client_order_id == "cio-position-1"
    assert event.position_id == "row-1"
    assert event.entry_order_id == "entry-1"
    assert event.closed_quantity == pytest.approx(0.001)
    assert event.remaining_quantity == 0
    assert event.exit_price == pytest.approx(120.5)
    assert event.exit_time == datetime(2026, 10, 9, 16, 0, 5, tzinfo=UTC)
    assert event.exit_order_id == "exit-1"
    assert event.pnl_basis == "fifo_attributed"
    assert event.pnl == pytest.approx(20.25)
    # the wire form is plain JSON: loading and dumping it again changes nothing
    assert json.loads(json.dumps(body)) == body


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["placed", "filled", "partial_fill", "rejected"])
async def test_any_event_type_survives_datetimes_decimals_and_naive_times(
    publisher, fake_nats_client, event_type
):
    """The fix is at the publisher: no event type can fail on a non-JSON field."""
    with patch("tradeengine.services.execution_event_publisher.settings") as s:
        s.nats_enabled = True
        s.nats_servers = "nats://localhost:4222"
        s.nats_topic_execution_events = "execution.events"
        publisher.set_client(fake_nats_client)
        ok = await publisher.publish(
            event_type=event_type,
            strategy_id="s1",
            order_id="o1",
            reason="r",
            extra={
                "fill_time": datetime(2026, 10, 9, 16, 0, 0),  # naive = UTC
                "fill_price": Decimal("100.10"),
                "nested": {"at": datetime(2026, 10, 9, 17, 0, tzinfo=UTC)},
                "tags": ("a", Decimal("1")),
            },
        )

    assert ok is True
    body = json.loads(fake_nats_client.publish.call_args.args[1].decode())
    assert body["fill_time"] == "2026-10-09T16:00:00+00:00"
    assert body["fill_price"] == 100.1
    assert body["nested"] == {"at": "2026-10-09T17:00:00+00:00"}
    assert body["tags"] == ["a", 1.0]


def test_json_safe_utc_mode_converts_offsets_and_leaves_default_mode_alone():
    from datetime import timedelta, timezone

    from tradeengine.json_safe import json_safe

    plus_two = datetime(2026, 10, 9, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    assert json_safe(plus_two, utc=True) == "2026-10-09T16:00:00+00:00"
    assert json_safe(plus_two) == "2026-10-09T18:00:00+02:00"  # unchanged for #755
