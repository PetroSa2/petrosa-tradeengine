import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.order import OrderSide, OrderType, TradeOrder
from shared.constants import UTC
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    exchange = Mock()
    exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "entry_order_123",
            "fill_price": 50000.0,
            "amount": 0.001,
        }
    )
    return exchange


@pytest.fixture
def dispatcher(mock_exchange):
    d = Dispatcher(exchange=mock_exchange)
    d.position_manager = Mock()
    d.position_manager.check_position_limits = AsyncMock(return_value=True)
    d.position_manager.check_daily_loss_limits = AsyncMock(return_value=True)
    d.position_manager.update_position = AsyncMock()
    d.position_manager.create_position_record = AsyncMock()
    return d


@pytest.mark.asyncio
async def test_oco_failure_causes_rollback(dispatcher):
    """
    Test that if _place_risk_management_orders fails,
    close_position_with_cleanup is called (Atomic Rollback).
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_123",
        position_id="pos_123",
        position_side="LONG",
        simulate=False,
    )

    # Mock _place_risk_management_orders to raise an exception
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )

    # Mock close_position_with_cleanup — mirror the real return shape:
    # position_closed=True signals the exchange-side close actually
    # succeeded (per its docstring at dispatcher.py:close_position_with_cleanup).
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success", "position_closed": True}
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_123")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify rollback was called
        dispatcher.close_position_with_cleanup.assert_called_once()
        assert result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_oco_timeout_causes_rollback(dispatcher):
    """
    Test that if _place_risk_management_orders times out,
    close_position_with_cleanup is called.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_124",
        position_id="pos_124",
        position_side="LONG",
        simulate=False,
    )

    # Mock _place_risk_management_orders to timeout
    dispatcher._place_risk_management_orders = AsyncMock(side_effect=TimeoutError())

    # Mock close_position_with_cleanup — position_closed=True mirrors the
    # real return shape and is now required for the rollback success path.
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success", "position_closed": True}
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_124")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify rollback was called
        dispatcher.close_position_with_cleanup.assert_called_once()
        assert result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_falls_back_when_position_id_none(dispatcher):
    """
    AC2/H2 of #424: when order.position_id is None, the rollback path MUST
    still issue a MARKET reduceOnly close via close_position_with_cleanup
    using (symbol, position_side, quantity). Pre-fix, this branch
    early-returned 'rolled_back_partial' WITHOUT touching Binance — the
    164× cascade in the 2026-05-30 incident.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_h2_1",
        position_id=None,  # ← the bug
        position_side="LONG",
        simulate=False,
    )

    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success", "position_closed": True}
    )

    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value=None)
        result = await dispatcher._execute_order_with_consensus(order)

    dispatcher.close_position_with_cleanup.assert_called_once()
    _, kwargs = dispatcher.close_position_with_cleanup.call_args
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["position_side"] == "LONG"
    assert kwargs["quantity"] == 0.001
    # 'partial' label is retained to flag that no local position record
    # existed — but the Binance position WAS closed.
    assert result["status"] == "rolled_back_partial"


@pytest.mark.asyncio
async def test_rollback_refetches_qty_from_binance_when_filled_zero(dispatcher):
    """
    AC2/H2: when filled_qty<=0, the rollback path MUST re-derive the
    position from Binance positionRisk before skipping. Only skip when
    Binance also reports zero.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.0,
        order_id="test_order_h2_2",
        position_id="pos_h2_2",
        position_side="LONG",
        simulate=False,
    )

    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )
    # result.amount is also 0 → fallback to Binance MUST trigger
    dispatcher.exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "entry_order_zero",
            "fill_price": 50000.0,
            "amount": 0.0,
        }
    )
    dispatcher.exchange.get_position_info = AsyncMock(
        return_value=[
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.5"},
            {"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": "1.0"},
        ]
    )
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success", "position_closed": True}
    )

    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_h2_2")
        result = await dispatcher._execute_order_with_consensus(order)

    dispatcher.close_position_with_cleanup.assert_called_once()
    _, kwargs = dispatcher.close_position_with_cleanup.call_args
    # Quantity MUST come from Binance positionRisk, not the zero local state
    assert kwargs["quantity"] == 0.5
    assert result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_skipped_only_when_binance_also_zero(dispatcher):
    """
    AC2/H2: when local filled_qty<=0 AND Binance positionRisk reports zero
    for the same (symbol, position_side), the SKIP branch is the correct
    outcome — there is no position to roll back.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.0,
        order_id="test_order_h2_3",
        position_id="pos_h2_3",
        position_side="LONG",
        simulate=False,
    )

    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )
    dispatcher.exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "entry_order_zero",
            "fill_price": 50000.0,
            "amount": 0.0,
        }
    )
    # Binance also reports zero — skip is correct
    dispatcher.exchange.get_position_info = AsyncMock(return_value=[])
    dispatcher.close_position_with_cleanup = AsyncMock()

    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_h2_3")
        result = await dispatcher._execute_order_with_consensus(order)

    dispatcher.close_position_with_cleanup.assert_not_called()
    assert result["status"] == "rolled_back_skipped"
    assert "binance_zero" in result.get("rollback_skipped_reason", "")


@pytest.mark.asyncio
async def test_rollback_failed_emits_alert_and_counter(dispatcher):
    """
    AC2: when the rollback path itself fails, the dispatcher MUST emit
    alerts.tradeengine.rollback_failed.<symbol> and increment
    petrosa_tradeengine_atomic_rollback_failed_total{symbol,reason} so
    ops can see the unhedged-position state even when the order-result
    return value is consumed silently.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_h2_4",
        position_id="pos_h2_4",
        position_side="LONG",
        simulate=False,
    )

    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )
    dispatcher.close_position_with_cleanup = AsyncMock(
        side_effect=RuntimeError("Binance reduceOnly rejected")
    )

    with (
        patch("tradeengine.dispatcher.alert_publisher") as mock_publisher,
        patch("tradeengine.dispatcher.atomic_rollback_failed_total") as mock_counter,
        patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm,
    ):
        mock_publisher.publish = AsyncMock(return_value=True)
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_h2_4")
        result = await dispatcher._execute_order_with_consensus(order)

    assert result["status"] == "rollback_failed"
    assert "Binance reduceOnly rejected" in result["rollback_error"]

    mock_counter.labels.assert_called_with(symbol="BTCUSDT", reason="RuntimeError")
    mock_counter.labels.return_value.inc.assert_called_once()

    mock_publisher.publish.assert_awaited_once()
    _, pub_kwargs = mock_publisher.publish.call_args
    assert pub_kwargs["alert_name"] == "rollback_failed.BTCUSDT"
    assert pub_kwargs["severity"] == "critical"
    payload = pub_kwargs["payload"]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["rollback_reason"] == "atomic_rollback_oco_failure"
    assert "Binance reduceOnly rejected" in payload["rollback_error"]


@pytest.mark.asyncio
async def test_rollback_failed_when_close_returns_position_closed_false(dispatcher):
    """
    Copilot review of #434: ``close_position_with_cleanup`` catches the
    exchange execute() exception internally and returns
    ``{"status": "failed", "position_closed": False}`` without raising.
    The rollback path MUST treat that as a failure — otherwise the
    position stays open on Binance while we log "rollback successful".
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_h2_returns_false",
        position_id="pos_returns_false",
        position_side="LONG",
        simulate=False,
    )

    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )
    # Real-shape return for the failure mode: no raise, but position_closed=False.
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={
            "position_closed": False,
            "oco_cancelled": False,
            "close_result": None,
            "status": "failed",
            "error": "Binance execute() returned status=REJECTED",
        }
    )

    with (
        patch("tradeengine.dispatcher.alert_publisher") as mock_publisher,
        patch("tradeengine.dispatcher.atomic_rollback_failed_total") as mock_counter,
        patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm,
    ):
        mock_publisher.publish = AsyncMock(return_value=True)
        mock_spm.create_strategy_position = AsyncMock(
            return_value="strat_pos_returns_false"
        )
        result = await dispatcher._execute_order_with_consensus(order)

    assert result["status"] == "rollback_failed"
    assert "did not close position" in result["rollback_error"]
    mock_counter.labels.assert_called_with(symbol="BTCUSDT", reason="RuntimeError")
    mock_counter.labels.return_value.inc.assert_called_once()
    mock_publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_binance_position_qty_one_way_mode_respects_sign(dispatcher):
    """
    Copilot review of #434: in one-way mode Binance returns
    ``positionSide="BOTH"`` for every position. A LONG rollback request
    must NOT match a BOTH row whose ``positionAmt`` is negative (= the
    account is actually short) — otherwise close_position_with_cleanup
    would send a sell reduceOnly on the wrong direction.
    """
    dispatcher.exchange.get_position_info = AsyncMock(
        return_value=[
            # ONE-WAY mode: a single BOTH row, negative => account is SHORT.
            {"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "-0.3"},
        ]
    )

    # LONG target: must NOT match the SHORT BOTH row → returns 0.0
    qty_long = await dispatcher._fetch_binance_position_qty("BTCUSDT", "LONG")
    assert qty_long == 0.0

    # SHORT target: matches the BOTH row → returns abs(positionAmt)
    qty_short = await dispatcher._fetch_binance_position_qty("BTCUSDT", "SHORT")
    assert qty_short == 0.3


@pytest.mark.asyncio
async def test_rollback_failure_is_handled(dispatcher):
    """
    Test that if close_position_with_cleanup itself fails during rollback,
    the failure path is handled and surfaced correctly.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_125",
        position_id="pos_125",
        position_side="LONG",
        simulate=False,
    )

    # Force risk management order placement to fail, triggering rollback.
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )

    # Now simulate a failure in the rollback itself.
    dispatcher.close_position_with_cleanup = AsyncMock(
        side_effect=Exception("Rollback Failed")
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_125")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify that rollback was attempted despite ultimately failing.
        dispatcher.close_position_with_cleanup.assert_called_once()
        # The dispatcher should surface a rollback failure status when rollback cannot complete.
        assert result["status"] == "rollback_failed"
        assert "Rollback Failed" in result["rollback_error"]
