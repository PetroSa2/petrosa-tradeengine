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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
