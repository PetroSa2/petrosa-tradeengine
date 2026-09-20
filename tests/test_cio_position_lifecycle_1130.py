"""Tests for the CIO position lifecycle channel (petrosa_k8s#1130).

Covers:
- consumer._position_lifecycle_handler: subject parsing (action/strategy_id
  extraction), malformed-subject and parse-error handling, delegation to the
  dispatcher.
- dispatcher.handle_cio_position_lifecycle_action: idempotent no-op when no
  open strategy position exists (race-condition acceptance criterion),
  routing to the per-action handlers, and unknown-action handling.
- _cio_exit_now / _cio_scale_out / _cio_modify_stops: each publishes an
  execution event with the client_order_id round-tripped from the strategy
  position record (so CIO can map it back to its PositionKey) and the
  correct reduce_only/position_status flags.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.consumer import SignalConsumer
from tradeengine.dispatcher import Dispatcher


def _open_position(**overrides) -> dict:
    base = {
        "strategy_position_id": "sp-1",
        "strategy_id": "iceberg_detector",
        "decision_id": "dec-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_quantity": 1.0,
        "entry_price": 50000.0,
        "stop_loss_price": 49000.0,
        "take_profit_price": 52000.0,
        "status": "open",
        "exchange_position_key": "BTCUSDT_LONG",
        # petrosa_k8s#1127: CIO's synthetic position_id, echoed as
        # Signal.client_order_id at entry.
        "client_order_id": "cio-position-id-abc123",
    }
    base.update(overrides)
    return base


@pytest.fixture
def dispatcher() -> Dispatcher:
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange = AsyncMock()
    d.oco_manager = AsyncMock()
    return d


# ---------------------------------------------------------------------------
# handle_cio_position_lifecycle_action — routing + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_open_position_is_a_noop(dispatcher: Dispatcher) -> None:
    """Race condition: position already closed elsewhere — must not error."""
    with patch("tradeengine.dispatcher.strategy_position_manager") as spm:
        spm.get_strategy_positions_by_strategy.return_value = []
        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="exit_now", strategy_id="ghost_strategy", decision_payload={}
        )
    assert result["positions_affected"] == 0


@pytest.mark.asyncio
async def test_unknown_action_is_ignored_not_raised(dispatcher: Dispatcher) -> None:
    with patch("tradeengine.dispatcher.strategy_position_manager") as spm:
        spm.get_strategy_positions_by_strategy.return_value = [_open_position()]
        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="some_future_action",
            strategy_id="iceberg_detector",
            decision_payload={},
        )
    assert result["positions_affected"] == 0


# ---------------------------------------------------------------------------
# EXIT_NOW
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_now_closes_and_publishes_with_round_tripped_client_order_id(
    dispatcher: Dispatcher,
) -> None:
    pos = _open_position()
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={
            "position_closed": True,
            "close_result": {"order_id": "close-order-1", "status": "FILLED"},
            "status": "success",
        }
    )
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as spm,
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
    ):
        spm.get_strategy_positions_by_strategy.return_value = [pos]
        pub.publish = AsyncMock(return_value=True)

        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="exit_now",
            strategy_id="iceberg_detector",
            decision_payload={"action": "EXIT_NOW", "justification": "risk"},
        )

    dispatcher.close_position_with_cleanup.assert_awaited_once()
    call_kw = dispatcher.close_position_with_cleanup.await_args.kwargs
    assert call_kw["position_id"] == "sp-1"
    assert call_kw["symbol"] == "BTCUSDT"
    assert call_kw["position_side"] == "LONG"
    assert call_kw["cio_audited"] is True

    pub.publish.assert_awaited_once()
    pub_kw = pub.publish.await_args.kwargs
    assert pub_kw["event_type"] == "filled"
    assert pub_kw["strategy_id"] == "iceberg_detector"
    assert pub_kw["client_order_id"] == "cio-position-id-abc123"
    assert pub_kw["extra"]["reduce_only"] is True
    assert pub_kw["extra"]["position_status"] == "closed"

    assert result["positions_affected"] == 1
    assert result["details"][0]["position_closed"] is True


@pytest.mark.asyncio
async def test_exit_now_does_not_publish_when_close_fails(
    dispatcher: Dispatcher,
) -> None:
    pos = _open_position()
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={
            "position_closed": False,
            "close_result": None,
            "status": "skipped_no_exchange_position",
        }
    )
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as spm,
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
    ):
        spm.get_strategy_positions_by_strategy.return_value = [pos]
        pub.publish = AsyncMock(return_value=True)

        await dispatcher.handle_cio_position_lifecycle_action(
            action="exit_now", strategy_id="iceberg_detector", decision_payload={}
        )

    pub.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# SCALE_OUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scale_out_places_reduce_only_partial_and_keeps_oco(
    dispatcher: Dispatcher,
) -> None:
    pos = _open_position(entry_quantity=2.0)
    dispatcher.exchange.execute = AsyncMock(
        return_value={"status": "FILLED", "order_id": "scale-1", "fill_price": 51000.0}
    )
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as spm,
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch.dict("os.environ", {"CIO_SCALE_OUT_DEFAULT_FRACTION": "0.5"}),
    ):
        spm.get_strategy_positions_by_strategy.return_value = [pos]
        spm.close_strategy_position = AsyncMock(
            return_value={
                "client_order_id": "cio-position-id-abc123",
                "position_status": "partial",
            }
        )
        pub.publish = AsyncMock(return_value=True)

        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="scale_out", strategy_id="iceberg_detector", decision_payload={}
        )

    # Half of entry_quantity=2.0 closed, OCO cancellation never invoked.
    dispatcher.exchange.execute.assert_awaited_once()
    placed_order = dispatcher.exchange.execute.await_args.args[0]
    assert placed_order.amount == pytest.approx(1.0)
    assert placed_order.reduce_only is True
    dispatcher.oco_manager.cancel_oco_pair.assert_not_called()

    spm.close_strategy_position.assert_awaited_once()
    close_kw = spm.close_strategy_position.await_args.kwargs
    assert close_kw["exit_quantity"] == pytest.approx(1.0)

    pub_kw = pub.publish.await_args.kwargs
    assert pub_kw["client_order_id"] == "cio-position-id-abc123"
    assert pub_kw["extra"]["position_status"] == "partial"
    assert result["details"][0]["quantity"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# MODIFY_STOPS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_stops_recomputes_prices_from_pct_and_rearms_oco(
    dispatcher: Dispatcher,
) -> None:
    pos = _open_position(side="LONG", entry_price=50000.0)
    dispatcher.oco_manager.cancel_oco_pair = AsyncMock(return_value=True)
    dispatcher.oco_manager.place_oco_orders = AsyncMock(
        return_value={"status": "success", "sl_order_id": "sl-2", "tp_order_id": "tp-2"}
    )
    with patch("tradeengine.dispatcher.strategy_position_manager") as spm:
        spm.get_strategy_positions_by_strategy.return_value = [pos]
        spm.set_strategy_position_orders = AsyncMock()

        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="modify_stops",
            strategy_id="iceberg_detector",
            decision_payload={"stop_loss_pct": 0.01, "take_profit_pct": 0.02},
        )

    dispatcher.oco_manager.cancel_oco_pair.assert_awaited_once_with(
        position_id="sp-1", symbol="BTCUSDT", position_side="LONG"
    )
    place_kw = dispatcher.oco_manager.place_oco_orders.await_args.kwargs
    assert place_kw["stop_loss_price"] == pytest.approx(49500.0)
    assert place_kw["take_profit_price"] == pytest.approx(51000.0)
    spm.set_strategy_position_orders.assert_awaited_once_with(
        "sp-1", sl_order_id="sl-2", tp_order_id="tp-2"
    )
    assert result["details"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_modify_stops_no_op_when_payload_has_no_pct(
    dispatcher: Dispatcher,
) -> None:
    pos = _open_position()
    with patch("tradeengine.dispatcher.strategy_position_manager") as spm:
        spm.get_strategy_positions_by_strategy.return_value = [pos]
        result = await dispatcher.handle_cio_position_lifecycle_action(
            action="modify_stops", strategy_id="iceberg_detector", decision_payload={}
        )
    dispatcher.oco_manager.cancel_oco_pair.assert_not_called()
    assert result["details"][0]["status"] == "no_op"


# ---------------------------------------------------------------------------
# consumer._position_lifecycle_handler — subject parsing + delegation
# ---------------------------------------------------------------------------


def _msg(subject: str, payload: dict) -> MagicMock:
    m = MagicMock()
    m.subject = subject
    m.data = json.dumps(payload).encode()
    return m


@pytest.mark.asyncio
async def test_position_handler_parses_action_and_strategy_id_from_subject() -> None:
    consumer = SignalConsumer()
    consumer.dispatcher = AsyncMock()
    consumer.dispatcher.handle_cio_position_lifecycle_action = AsyncMock(
        return_value={"positions_affected": 1}
    )

    await consumer._position_lifecycle_handler(
        _msg("cio.position.exit_now.iceberg_detector", {"action": "EXIT_NOW"})
    )

    consumer.dispatcher.handle_cio_position_lifecycle_action.assert_awaited_once_with(
        action="exit_now",
        strategy_id="iceberg_detector",
        decision_payload={"action": "EXIT_NOW"},
    )


@pytest.mark.asyncio
async def test_position_handler_ignores_malformed_subject() -> None:
    consumer = SignalConsumer()
    consumer.dispatcher = AsyncMock()
    consumer.dispatcher.handle_cio_position_lifecycle_action = AsyncMock()

    await consumer._position_lifecycle_handler(_msg("cio.position.exit_now", {}))

    consumer.dispatcher.handle_cio_position_lifecycle_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_handler_survives_unparseable_payload() -> None:
    consumer = SignalConsumer()
    consumer.dispatcher = AsyncMock()
    consumer.dispatcher.handle_cio_position_lifecycle_action = AsyncMock(
        return_value={"positions_affected": 0}
    )
    msg = MagicMock()
    msg.subject = "cio.position.exit_now.iceberg_detector"
    msg.data = b"not-json"

    await consumer._position_lifecycle_handler(msg)

    consumer.dispatcher.handle_cio_position_lifecycle_action.assert_awaited_once_with(
        action="exit_now", strategy_id="iceberg_detector", decision_payload={}
    )


@pytest.mark.asyncio
async def test_position_handler_noop_when_no_dispatcher() -> None:
    consumer = SignalConsumer()
    consumer.dispatcher = None

    # Must not raise even though there's nothing to delegate to.
    await consumer._position_lifecycle_handler(
        _msg("cio.position.exit_now.iceberg_detector", {})
    )
