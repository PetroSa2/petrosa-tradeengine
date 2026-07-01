"""Regression tests for issue #490.

Startup orphaned-TP cancellation sent ``orderId`` (and, due to swapped
positional args at the call site, a *symbol string*) to the regular
``futures_cancel_order`` endpoint for **algo** orders, yielding
``APIError(-1102)`` on every pod restart. Orphaned closePosition TP/SL orders
live in ``/openAlgoOrders`` (disjoint from ``/openOrders``, #483) and must be
cancelled with ``algoId`` via ``cancel_algo_order``.

These tests assert:
  * scan-time recording of algo-ness (``tp_is_algo``/``sl_is_algo``) so the
    cancel path routes by source, never by id length (AC2);
  * the dispatcher routes algo orphans to ``cancel_algo_order`` (AC1/AC2);
  * the standard-order path calls ``cancel_order(symbol, order_id)`` in the
    correct argument order (the swapped-arg root cause);
  * a ``-4029`` ("order does not exist") is swallowed as already-gone (AC6).

All four FAIL against ``main`` (no ``_cancel_orphaned_order`` helper, no
``cancel_algo_order`` method, no scan-time algo flag).
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


class _FakeAPIError(Exception):
    """Minimal stand-in for binance's APIError (carries a numeric ``code``)."""

    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or f"APIError(code={code})")
        self.code = code


@pytest.fixture
def oco_manager():
    exchange = MagicMock()
    exchange.cancel_algo_order = AsyncMock()
    exchange.cancel_order = AsyncMock()
    return OCOManager(exchange=exchange, logger=logging.getLogger("test-490"))


class TestOrphanAlgoCancelRouting:
    @pytest.mark.asyncio
    async def test_algo_orphan_routes_to_cancel_algo_order(self, oco_manager):
        """An orphan known to be an algo order must cancel via algoId."""
        await oco_manager._cancel_orphaned_order(
            "LINKUSDT", "1000000110444048", is_algo=True
        )

        oco_manager.exchange.cancel_algo_order.assert_awaited_once_with(
            "LINKUSDT", "1000000110444048"
        )
        oco_manager.exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_standard_orphan_uses_correct_arg_order(self, oco_manager):
        """Non-algo orphan must call cancel_order(symbol, order_id) — not swapped."""
        await oco_manager._cancel_orphaned_order("ADAUSDT", "12345", is_algo=False)

        # The root-cause bug passed (order_id, symbol). Assert correct order.
        oco_manager.exchange.cancel_order.assert_awaited_once_with("ADAUSDT", "12345")
        oco_manager.exchange.cancel_algo_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_algo_cancel_4029_is_swallowed(self, oco_manager):
        """-4029 (already cancelled out-of-band) must not raise or log ERROR."""
        oco_manager.exchange.cancel_algo_order = AsyncMock(
            side_effect=_FakeAPIError(-4029, "order does not exist")
        )

        # Must not raise.
        await oco_manager._cancel_orphaned_order("BNBUSDT", "999", is_algo=True)

        oco_manager.exchange.cancel_algo_order.assert_awaited_once_with(
            "BNBUSDT", "999"
        )


class TestOrphanScanRecordsAlgoNess:
    @pytest.mark.asyncio
    async def test_reconcile_marks_unpaired_algo_tp_as_algo(self):
        """An unpaired TP sourced from /openAlgoOrders is recorded tp_is_algo=True."""
        exchange = MagicMock()
        exchange.client = MagicMock()  # truthy → reconcile proceeds
        exchange.get_open_algo_orders = AsyncMock(
            return_value=[
                {
                    "algoId": 1000000110444048,
                    "symbol": "LINKUSDT",
                    "type": "TAKE_PROFIT_MARKET",
                    "positionSide": "LONG",
                    "origQty": "1.0",
                    "createTime": 1,
                }
            ]
        )
        oco = OCOManager(exchange=exchange, logger=logging.getLogger("test-490b"))
        # Don't spin up the background monitor loop in a unit test.
        oco.start_monitoring = AsyncMock()

        rebuilt = await oco.reconcile_from_exchange()

        assert rebuilt == 1
        entries = oco.active_oco_pairs["LINKUSDT_LONG"]
        orphan = next(e for e in entries if e.get("orphaned"))
        assert orphan["tp_is_algo"] is True
        assert orphan["sl_order_id"] is None
        assert str(orphan["tp_order_id"]) == "1000000110444048"


class TestCancelAlgoOrderCallShape:
    """The real cancel_algo_order must hit the algo DELETE endpoint correctly.

    Regression for the v1.2.17-r147 production failure (#490): the first fix
    used ``params=``, which raises ``KeyError('data')`` inside python-binance's
    ``_request``. The working shape — proven by ``cancel_oco_pair`` (#334) — is
    ``force_params=True`` + ``data=``. This test exercises the real method (no
    mock of cancel_algo_order itself) and FAILS on the params= version.
    """

    @pytest.mark.asyncio
    async def test_uses_force_params_and_data(self):
        from tradeengine.exchange.binance import BinanceFuturesExchange

        exchange = BinanceFuturesExchange()
        exchange.initialized = True
        exchange.client = MagicMock()
        exchange.client._request_futures_api = MagicMock(
            return_value={"algoId": 1000000110444048, "symbol": "LINKUSDT"}
        )

        await exchange.cancel_algo_order("LINKUSDT", 1000000110444048)

        exchange.client._request_futures_api.assert_called_once_with(
            "delete",
            "algoOrder",
            signed=True,
            force_params=True,
            data={"symbol": "LINKUSDT", "algoId": 1000000110444048},
        )
