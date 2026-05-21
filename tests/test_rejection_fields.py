"""Tests for the P6.2 TradeOrder rejection fields (#608).

Covers:
  * Three new optional fields default to None and round-trip cleanly.
  * Each `rejection_source` literal validates correctly; unknown values
    are rejected by Pydantic.
  * Backwards-compat: deserializing a pre-#608 TradeOrder dict (with no
    rejection_* keys) succeeds.
  * The `mark_rejected` helper sets status, all three fields, and
    `updated_at` in one call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder


def _bare_order(**overrides) -> TradeOrder:
    base = {
        "symbol": "BTCUSDT",
        "type": OrderType.MARKET.value,
        "side": OrderSide.BUY.value,
        "amount": 0.01,
    }
    base.update(overrides)
    return TradeOrder(**base)


def test_rejection_fields_default_to_none():
    order = _bare_order()
    assert order.rejection_reason is None
    assert order.rejected_at is None
    assert order.rejection_source is None


@pytest.mark.parametrize(
    "source",
    [
        "risk_check",
        "exchange",
        "stale_signal",
        "whitelist",
        "balance",
        "validation",
    ],
)
def test_each_known_rejection_source_validates(source):
    order = _bare_order(
        rejection_reason="something failed",
        rejection_source=source,
        rejected_at=datetime.now(UTC),
    )
    assert order.rejection_source == source


def test_unknown_rejection_source_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _bare_order(
            rejection_reason="oops",
            rejection_source="vibes_based",  # not in the literal set
            rejected_at=datetime.now(UTC),
        )
    assert "rejection_source" in str(exc_info.value)


def test_pre_change_order_deserializes_without_rejection_fields():
    """Backwards-compat AC: pre-#608 TradeOrder dicts must still parse."""
    pre_change_dict = {
        "symbol": "BTCUSDT",
        "type": "limit",
        "side": "buy",
        "amount": 0.05,
        "status": "pending",
        "filled_amount": 0,
        "exchange": "binance",
        "simulate": True,
        "reduce_only": False,
        "strategy_metadata": {},
        "meta": {},
        # No rejection_reason / rejected_at / rejection_source — pre-#608.
    }
    order = TradeOrder(**pre_change_dict)
    assert order.rejection_reason is None
    assert order.rejected_at is None
    assert order.rejection_source is None


def test_round_trip_with_rejection_fields():
    rejected_at = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    order = _bare_order(
        rejection_reason="balance_below_minimum",
        rejection_source="balance",
        rejected_at=rejected_at,
        status=OrderStatus.REJECTED,
    )
    dumped = order.model_dump()
    assert dumped["rejection_reason"] == "balance_below_minimum"
    assert dumped["rejection_source"] == "balance"
    assert dumped["rejected_at"] == rejected_at
    rebuilt = TradeOrder(**dumped)
    assert rebuilt.rejection_source == "balance"
    assert rebuilt.rejected_at == rejected_at


def test_json_serialization_roundtrip():
    """JSON encode/decode must preserve the rejection fields."""
    import json

    rejected_at = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    order = _bare_order(
        rejection_reason="429 rate-limit hit",
        rejection_source="exchange",
        rejected_at=rejected_at,
        status=OrderStatus.REJECTED,
    )
    blob = order.model_dump_json()
    payload = json.loads(blob)
    assert payload["rejection_reason"] == "429 rate-limit hit"
    assert payload["rejection_source"] == "exchange"
    rebuilt = TradeOrder.model_validate_json(blob)
    assert rebuilt.rejection_reason == "429 rate-limit hit"
    assert rebuilt.rejection_source == "exchange"
    # Pydantic v2 deserializes the ISO ts back to a tz-aware datetime.
    assert abs((rebuilt.rejected_at - rejected_at).total_seconds()) < 1


def test_mark_rejected_sets_status_and_all_fields():
    order = _bare_order()
    before = datetime.now(UTC)
    returned = order.mark_rejected(
        source="risk_check",
        reason="max_position_size_exceeded",
    )
    after = datetime.now(UTC)

    assert returned is order  # fluent return
    assert order.status == OrderStatus.REJECTED
    assert order.rejection_source == "risk_check"
    assert order.rejection_reason == "max_position_size_exceeded"
    assert order.rejected_at is not None
    assert (
        before - timedelta(seconds=1)
        <= order.rejected_at
        <= after + timedelta(seconds=1)
    )
    assert order.updated_at == order.rejected_at


def test_mark_rejected_accepts_explicit_timestamp():
    order = _bare_order()
    ts = datetime(2026, 5, 21, 9, 0, 0, tzinfo=UTC)
    order.mark_rejected(
        source="exchange",
        reason="4001: insufficient margin",
        rejected_at=ts,
    )
    assert order.rejected_at == ts
    assert order.updated_at == ts


def test_mark_rejected_can_be_chained():
    """Fluent return makes single-line rejection at call-sites."""
    order = _bare_order().mark_rejected(
        source="stale_signal", reason="signal age 65s > max 60s"
    )
    assert order.status == OrderStatus.REJECTED
    assert order.rejection_source == "stale_signal"
