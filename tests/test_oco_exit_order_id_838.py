"""A triggered TP/SL close records the filled order's ``orderId``, not the algo id (petrosa-tradeengine#838).

Binance conditional legs are algo orders (``algoId``). When one triggers, the fill is a different, standard
order, and its exchange fills (``userTrades``) carry that order's ``orderId``. Strategy rows used to record the
algo id as ``exit_order_id``, so no TP/SL close could be linked to its fills.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from shared.mysql_client import DataManagerPositionClient
from tests.test_oco_algo_cancel_650 import (  # noqa: F401  (fixtures)
    SYMBOL,
    _explicit_mode,
    _no_backoff,
    clean_strategy_positions,
    client,
    oco,
)
from tradeengine.dispatcher import OCOManager
from tradeengine.strategy_position_manager import (
    StrategyPositionManager,
    strategy_position_manager,
)

ALGO_PAIR = {"tp_order_id": "7001", "sl_order_id": "7002", "tp_is_algo": True}


def test_algo_leg_records_the_filled_order_id_and_keeps_the_algo_id_apart():
    exit_order_id, exit_algo_id = OCOManager._oco_exit_ids(
        "7001", ALGO_PAIR, {"orderId": 9001, "avgPrice": "95.0"}
    )
    assert (exit_order_id, exit_algo_id) == ("9001", "7001")


def test_unresolved_algo_fill_has_no_exit_order_id_rather_than_the_algo_id():
    exit_order_id, exit_algo_id = OCOManager._oco_exit_ids("7001", ALGO_PAIR, {})
    assert exit_order_id is None
    assert exit_algo_id == "7001"


def test_standard_leg_id_is_its_order_id():
    pair = {"tp_order_id": "555", "tp_is_algo": False}
    assert OCOManager._oco_exit_ids("555", pair, {"orderId": 555}) == ("555", None)


@pytest.mark.asyncio
async def test_close_records_both_ids_and_they_are_persisted():
    manager = StrategyPositionManager()
    manager.strategy_positions["sp-838"] = {
        "strategy_position_id": "sp-838",
        "status": "open",
        "side": "LONG",
        "symbol": "BTCUSDT",
        "entry_price": 100.0,
        "entry_quantity": 2.0,
        "exchange_position_key": "BTCUSDT_LONG",
        "strategy_id": "test",
    }
    with patch.object(
        StrategyPositionManager, "_update_strategy_position_closure", AsyncMock()
    ):
        await manager.close_strategy_position(
            strategy_position_id="sp-838",
            exit_price=105.0,
            exit_quantity=2.0,
            close_reason="take_profit",
            exit_order_id="9001",
            exit_algo_id="7001",
        )
    row = manager.strategy_positions["sp-838"]
    assert row["exit_order_id"] == "9001"
    assert row["exit_algo_id"] == "7001"
    persisted = DataManagerPositionClient._columns(
        row, DataManagerPositionClient._STRATEGY_POSITION_COLUMNS
    )
    assert persisted["exit_order_id"] == "9001"
    assert persisted["exit_algo_id"] == "7001"


def _strategy_row(tp_id: str, sl_id: str) -> dict[str, Any]:
    return {
        "strategy_position_id": "spid-dot",
        "strategy_id": "momentum",
        "symbol": SYMBOL,
        "side": "LONG",
        "entry_price": 4.0,
        "entry_quantity": 82.8,
        "status": "open",
        "exchange_position_key": f"{SYMBOL}_LONG",
        "sl_order_id": sl_id,
        "tp_order_id": tp_id,
    }


@pytest.mark.asyncio
async def test_triggered_tp_close_links_to_the_fill_order_id(
    oco: OCOManager, client: Any, clean_strategy_positions: dict[str, Any]
) -> None:
    sl_id = client.add_algo_order(
        symbol=SYMBOL, orderType="STOP_MARKET", quantity="82.8", triggerPrice="3.6"
    )
    tp_id = client.add_algo_order(
        symbol=SYMBOL,
        orderType="TAKE_PROFIT_MARKET",
        quantity="82.8",
        triggerPrice="4.4",
    )
    assert await oco.reconcile_from_exchange() == 1
    clean_strategy_positions["spid-dot"] = _strategy_row(tp_id, sl_id)
    close_mock = AsyncMock(return_value={"decision_id": "d1"})
    fill = {"orderId": 990001, "avgPrice": "4.4", "executedQty": "82.8"}
    with (
        patch.object(strategy_position_manager, "close_strategy_position", close_mock),
        patch.object(oco, "_fetch_oco_exit_details", return_value=fill),
    ):
        client.fill_leg(tp_id, 0.0)
        await oco._monitor_iteration()

    close_mock.assert_awaited_once()
    kwargs = close_mock.await_args.kwargs
    assert kwargs["exit_order_id"] == "990001"
    assert kwargs["exit_algo_id"] == str(tp_id)
    assert kwargs["close_reason"] == "take_profit"


@pytest.mark.asyncio
async def test_unresolved_triggered_close_never_records_the_algo_id_as_the_order_id(
    oco: OCOManager, client: Any, clean_strategy_positions: dict[str, Any]
) -> None:
    sl_id = client.add_algo_order(
        symbol=SYMBOL, orderType="STOP_MARKET", quantity="82.8", triggerPrice="3.6"
    )
    tp_id = client.add_algo_order(
        symbol=SYMBOL,
        orderType="TAKE_PROFIT_MARKET",
        quantity="82.8",
        triggerPrice="4.4",
    )
    assert await oco.reconcile_from_exchange() == 1
    clean_strategy_positions["spid-dot"] = _strategy_row(tp_id, sl_id)
    close_mock = AsyncMock(return_value={"decision_id": "d1"})
    with patch.object(strategy_position_manager, "close_strategy_position", close_mock):
        client.fill_leg(sl_id, 0.0)  # the fake cannot resolve the algo order
        await oco._monitor_iteration()

    kwargs = close_mock.await_args.kwargs
    assert kwargs["exit_order_id"] is None
    assert kwargs["exit_algo_id"] == str(sl_id)
    assert kwargs["pnl_unknown"] is True
