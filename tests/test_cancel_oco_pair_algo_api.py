"""
Regression test for issue #334: cancel_oco_pair must use the algo-order DELETE API.

Root cause: cancel_oco_pair() was calling futures_cancel_batch_orders() which silently
succeeds for conditional algo orders without actually cancelling them, causing orphaned
orders to accumulate until the 100-order account cap was reached.

Fix: cancel_oco_pair() must call DELETE /fapi/v1/algo/algoOrder (via _request_futures_api
with force_params=True) for each SL/TP algo order individually.
"""

import logging
from unittest.mock import MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.client.futures_cancel_batch_orders.return_value = []
    return exchange


@pytest.fixture
def oco_manager(mock_exchange):
    return OCOManager(exchange=mock_exchange, logger=logging.getLogger("test"))


class TestCancelOcoPairAlgoOrderAPI:
    """
    Verifies that cancel_oco_pair() cancels SL/TP orders via the Binance algo-order
    DELETE endpoint, not via futures_cancel_batch_orders which silently ignores them.
    """

    @pytest.mark.asyncio
    async def test_cancel_oco_pair_calls_algo_api_twice(
        self, oco_manager, mock_exchange
    ):
        """Reproducer for issue #334: cancel_oco_pair must call algo-order DELETE API for SL and TP."""
        algo_cancel_calls = []
        mock_exchange.client._request_futures_api = MagicMock(
            side_effect=lambda *a, **kw: algo_cancel_calls.append((a, kw))
        )

        oco_manager.active_oco_pairs["BTCUSDT_LONG"] = [
            {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "sl_order_id": 1000000049928499,
                "tp_order_id": 1000000049928500,
                "status": "active",
                "reconciled": False,
                "position_id": "test-001",
                "strategy_position_id": "strat-001",
                "exchange_position_key": "BTCUSDT_LONG",
            }
        ]

        result = await oco_manager.cancel_oco_pair(
            position_id="test-001", symbol="BTCUSDT", position_side="LONG"
        )

        assert len(algo_cancel_calls) == 2, (
            "cancel_oco_pair must call algo-order DELETE API for SL and TP"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_oco_pair_uses_correct_algo_api_endpoint(
        self, oco_manager, mock_exchange
    ):
        """Verify method, path, signed, force_params, and algoId payload for each cancel call."""
        algo_cancel_calls = []
        mock_exchange.client._request_futures_api = MagicMock(
            side_effect=lambda *a, **kw: algo_cancel_calls.append((a, kw))
        )

        sl_id = 1000000049928499
        tp_id = 1000000049928500
        oco_manager.active_oco_pairs["ETHUSDT_SHORT"] = [
            {
                "symbol": "ETHUSDT",
                "position_side": "SHORT",
                "sl_order_id": sl_id,
                "tp_order_id": tp_id,
                "status": "active",
                "reconciled": False,
                "position_id": "test-002",
                "strategy_position_id": "strat-002",
                "exchange_position_key": "ETHUSDT_SHORT",
            }
        ]

        await oco_manager.cancel_oco_pair(
            position_id="test-002", symbol="ETHUSDT", position_side="SHORT"
        )

        assert len(algo_cancel_calls) == 2

        for args, kwargs in algo_cancel_calls:
            assert args[0] == "delete", "Must use DELETE method"
            assert args[1] == "algoOrder", "Must target algoOrder endpoint"
            assert kwargs.get("signed") is True, "Must be signed request"
            assert kwargs.get("force_params") is True, (
                "DELETE algo orders require force_params=True to send params as query string"
            )
            data = kwargs.get("data", {})
            assert data.get("symbol") == "ETHUSDT"
            assert data.get("algoId") in (sl_id, tp_id)

        algo_ids_cancelled = {c["data"]["algoId"] for _, c in algo_cancel_calls}
        assert algo_ids_cancelled == {sl_id, tp_id}, (
            "Both SL and TP algo orders must be cancelled"
        )

    @pytest.mark.asyncio
    async def test_futures_cancel_batch_orders_not_called(
        self, oco_manager, mock_exchange
    ):
        """Verify the wrong (silent-fail) API is never invoked."""
        mock_exchange.client._request_futures_api = MagicMock(return_value=None)

        oco_manager.active_oco_pairs["XRPUSDT_LONG"] = [
            {
                "symbol": "XRPUSDT",
                "position_side": "LONG",
                "sl_order_id": 999001,
                "tp_order_id": 999002,
                "status": "active",
                "reconciled": False,
                "position_id": "test-003",
                "strategy_position_id": "strat-003",
                "exchange_position_key": "XRPUSDT_LONG",
            }
        ]

        await oco_manager.cancel_oco_pair(
            position_id="test-003", symbol="XRPUSDT", position_side="LONG"
        )

        mock_exchange.client.futures_cancel_batch_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_oco_pair_marks_status_cancelled_on_success(
        self, oco_manager, mock_exchange
    ):
        """After successful cancellation the pair status must be updated in memory."""
        mock_exchange.client._request_futures_api = MagicMock(return_value=None)

        pair = {
            "symbol": "BNBUSDT",
            "position_side": "LONG",
            "sl_order_id": 111,
            "tp_order_id": 222,
            "status": "active",
            "reconciled": False,
            "position_id": "test-004",
            "strategy_position_id": "strat-004",
            "exchange_position_key": "BNBUSDT_LONG",
        }
        oco_manager.active_oco_pairs["BNBUSDT_LONG"] = [pair]

        result = await oco_manager.cancel_oco_pair(
            position_id="test-004", symbol="BNBUSDT", position_side="LONG"
        )

        assert result is True
        assert pair["status"] == "cancelled"
