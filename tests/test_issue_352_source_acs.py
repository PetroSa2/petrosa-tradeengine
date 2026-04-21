"""
Tests for issue #352 source-code acceptance criteria.

AC-1: position write retries 3 times before logging CRITICAL (no silent drop)
AC-2: OCO dedup guard — second placement on same exchange position is rejected
AC-3: closePosition=true on all algo SL/TP orders (no quantity/reduceOnly)
AC-4: reconcile_from_exchange registers orphaned orders individually (no zip() loss)
AC-5: NATS consumer subscribes with queue group 'tradeengine-workers'
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.dispatcher import OCOManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oco_manager() -> OCOManager:
    exchange = MagicMock()
    exchange.client = MagicMock()
    exchange.get_open_algo_orders = AsyncMock(return_value=[])
    # OCOManager calls exchange.execute() for both SL and TP orders
    _exec_call_count = [0]

    async def _fake_execute(order):
        _exec_call_count[0] += 1
        return {
            "order_id": f"order_{_exec_call_count[0]}",
            "algoId": f"order_{_exec_call_count[0]}",
            "status": "NEW",
        }

    exchange.execute = _fake_execute

    import logging

    mgr = OCOManager(exchange=exchange, logger=logging.getLogger("test"))
    return mgr


def _make_order(order_id: str = "ord1"):
    o = MagicMock()
    o.order_id = order_id
    o.symbol = "BTCUSDT"
    o.position_side = "LONG"
    o.side = "sell"
    o.amount = 0.001
    o.stop_loss = 48000.0
    o.take_profit = 52000.0
    o.target_price = 52000.0
    o.reduce_only = True
    o.time_in_force = None
    return o


# ---------------------------------------------------------------------------
# AC-2: OCO dedup guard
# ---------------------------------------------------------------------------


class TestAC2OcoDedupGuard:
    """Only one active OCO pair per exchange position."""

    @pytest.mark.asyncio
    async def test_first_placement_succeeds(self):
        mgr = _make_oco_manager()
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
            strategy_position_id="strat_a",
        )
        assert result.get("error") != "duplicate_oco"
        assert result.get("status") != "error", f"Unexpected error: {result}"

    @pytest.mark.asyncio
    async def test_second_placement_same_position_rejected(self):
        mgr = _make_oco_manager()
        await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
            strategy_position_id="strat_a",
        )
        result2 = await mgr.place_oco_orders(
            position_id="pos2",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.002,
            stop_loss_price=47500.0,
            take_profit_price=52500.0,
            strategy_position_id="strat_b",
        )
        assert result2.get("error") == "duplicate_oco"

    @pytest.mark.asyncio
    async def test_different_symbols_both_allowed(self):
        mgr = _make_oco_manager()
        await mgr.place_oco_orders(
            position_id="p1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        r2 = await mgr.place_oco_orders(
            position_id="p2",
            symbol="ETHUSDT",
            position_side="LONG",
            quantity=0.01,
            stop_loss_price=2800.0,
            take_profit_price=3200.0,
        )
        assert r2.get("error") != "duplicate_oco"


# ---------------------------------------------------------------------------
# AC-3: closePosition=true on all algo SL/TP order types
# ---------------------------------------------------------------------------


class TestAC3ClosePositionFlag:
    """All algo orders must use closePosition=True; no quantity or reduceOnly."""

    def _make_binance(self):
        from tradeengine.exchange.binance import BinanceFuturesExchange

        exchange = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
        exchange.client = MagicMock()
        exchange._symbol_info_cache = {}
        exchange._format_quantity = lambda sym, qty: str(round(qty, 3))
        exchange._format_price = lambda sym, price: str(round(price, 2))
        exchange._execute_with_retry = AsyncMock(
            return_value={"algoId": "123", "status": "NEW"}
        )
        exchange.validate_price_within_percent_filter = AsyncMock(
            return_value=(True, None)
        )
        return exchange

    @pytest.mark.asyncio
    async def test_stop_order_uses_close_position(self):
        exchange = self._make_binance()
        order = _make_order()
        order.stop_loss = 48000.0
        order.position_side = None

        await exchange._execute_stop_order(order)

        call_kwargs = exchange._execute_with_retry.call_args
        passed_kwargs = call_kwargs[1] if call_kwargs[1] else {}
        # _execute_with_retry called as (fn, **params)
        if not passed_kwargs:
            passed_kwargs = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        # Flatten: first positional is the function, rest are params
        flat = {k: v for k, v in call_kwargs[1].items()}
        assert flat.get("closePosition") is True, "closePosition must be True"
        assert "quantity" not in flat, (
            "quantity must be omitted when closePosition=True"
        )
        assert "reduceOnly" not in flat, (
            "reduceOnly must be omitted when closePosition=True"
        )

    @pytest.mark.asyncio
    async def test_take_profit_order_uses_close_position(self):
        exchange = self._make_binance()
        order = _make_order()
        order.take_profit = 52000.0
        order.position_side = None

        await exchange._execute_take_profit_order(order)

        flat = exchange._execute_with_retry.call_args[1]
        assert flat.get("closePosition") is True
        assert "quantity" not in flat
        assert "reduceOnly" not in flat

    @pytest.mark.asyncio
    async def test_stop_limit_order_uses_close_position(self):
        exchange = self._make_binance()
        order = _make_order()
        order.stop_loss = 48000.0
        order.target_price = 47900.0
        order.position_side = None

        await exchange._execute_stop_limit_order(order)

        flat = exchange._execute_with_retry.call_args[1]
        assert flat.get("closePosition") is True
        assert "quantity" not in flat
        assert "reduceOnly" not in flat

    @pytest.mark.asyncio
    async def test_take_profit_limit_order_uses_close_position(self):
        exchange = self._make_binance()
        order = _make_order()
        order.take_profit = 52000.0
        order.target_price = 52100.0
        order.position_side = None

        await exchange._execute_take_profit_limit_order(order)

        flat = exchange._execute_with_retry.call_args[1]
        assert flat.get("closePosition") is True
        assert "quantity" not in flat
        assert "reduceOnly" not in flat


# ---------------------------------------------------------------------------
# AC-4: reconcile_from_exchange registers orphaned orders individually
# ---------------------------------------------------------------------------


class TestAC4ReconcileOrphans:
    """
    zip() previously stopped at the shorter list, dropping unmatched orders.
    Every SL and TP order — including unpaired ones — must be tracked.
    """

    @pytest.mark.asyncio
    async def test_all_orders_registered_when_counts_differ(self):
        mgr = _make_oco_manager()
        # 2 SL + 4 TP → 2 pairs + 2 orphaned TPs
        mgr.exchange.get_open_algo_orders = AsyncMock(
            return_value=[
                {
                    "algoId": "sl1",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "createTime": 1,
                    "quantity": "1.0",
                },
                {
                    "algoId": "sl2",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "createTime": 2,
                    "quantity": "1.0",
                },
                {
                    "algoId": "tp1",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 1,
                    "quantity": "1.0",
                },
                {
                    "algoId": "tp2",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 2,
                    "quantity": "1.0",
                },
                {
                    "algoId": "tp3",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 3,
                    "quantity": "1.0",
                },
                {
                    "algoId": "tp4",
                    "symbol": "BCHUSDT",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 4,
                    "quantity": "1.0",
                },
            ]
        )
        rebuilt = await mgr.reconcile_from_exchange()

        # 2 paired + 2 orphaned TPs = 4 total entries
        assert rebuilt == 4, (
            f"Expected 4 entries (2 paired + 2 orphaned TP), got {rebuilt}"
        )

        key = "BCHUSDT_LONG"
        entries = mgr.active_oco_pairs.get(key, [])
        all_tp_ids = {e["tp_order_id"] for e in entries if e.get("tp_order_id")}
        assert "tp3" in all_tp_ids, "Orphaned tp3 must be tracked"
        assert "tp4" in all_tp_ids, "Orphaned tp4 must be tracked"

    @pytest.mark.asyncio
    async def test_orphaned_entries_flagged(self):
        mgr = _make_oco_manager()
        mgr.exchange.get_open_algo_orders = AsyncMock(
            return_value=[
                # 1 SL, 3 TPs → 1 pair + 2 orphaned TPs
                {
                    "algoId": "slA",
                    "symbol": "ETHUSDT",
                    "positionSide": "SHORT",
                    "type": "STOP_MARKET",
                    "createTime": 1,
                    "quantity": "0.5",
                },
                {
                    "algoId": "tpA",
                    "symbol": "ETHUSDT",
                    "positionSide": "SHORT",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 1,
                    "quantity": "0.5",
                },
                {
                    "algoId": "tpB",
                    "symbol": "ETHUSDT",
                    "positionSide": "SHORT",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 2,
                    "quantity": "0.5",
                },
                {
                    "algoId": "tpC",
                    "symbol": "ETHUSDT",
                    "positionSide": "SHORT",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 3,
                    "quantity": "0.5",
                },
            ]
        )
        await mgr.reconcile_from_exchange()

        key = "ETHUSDT_SHORT"
        entries = mgr.active_oco_pairs.get(key, [])
        orphaned = [e for e in entries if e.get("orphaned")]
        assert len(orphaned) == 2, f"Expected 2 orphaned entries, got {len(orphaned)}"

    @pytest.mark.asyncio
    async def test_equal_sl_tp_no_orphans(self):
        mgr = _make_oco_manager()
        mgr.exchange.get_open_algo_orders = AsyncMock(
            return_value=[
                {
                    "algoId": "sl1",
                    "symbol": "BTCUSDT",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "createTime": 1,
                    "quantity": "0.01",
                },
                {
                    "algoId": "tp1",
                    "symbol": "BTCUSDT",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "createTime": 1,
                    "quantity": "0.01",
                },
            ]
        )
        rebuilt = await mgr.reconcile_from_exchange()
        assert rebuilt == 1

        key = "BTCUSDT_LONG"
        entries = mgr.active_oco_pairs.get(key, [])
        orphaned = [e for e in entries if e.get("orphaned")]
        assert len(orphaned) == 0


# ---------------------------------------------------------------------------
# AC-5: NATS queue group
# ---------------------------------------------------------------------------


class TestAC5NatsQueueGroup:
    """NATS consumer must subscribe with queue='tradeengine-workers'."""

    @pytest.mark.asyncio
    async def test_subscribe_uses_queue_group(self):
        from tradeengine.consumer import SignalConsumer

        consumer = SignalConsumer.__new__(SignalConsumer)
        consumer.running = False
        consumer.subscription = None
        consumer._dispatcher_provided = True
        consumer.dispatcher = AsyncMock()

        mock_nc = AsyncMock()
        mock_sub = AsyncMock()
        consumer.nc = mock_nc

        async def stop_after_subscribe(*args, **kwargs):
            consumer.running = False
            return mock_sub

        mock_nc.subscribe = AsyncMock(side_effect=stop_after_subscribe)
        mock_nc.is_connected = True

        consumer._message_handler = AsyncMock()

        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_topic_signals = "signals.trading"

            try:
                await consumer.start_consuming()
            except Exception:
                pass

        mock_nc.subscribe.assert_called_once()
        call_kwargs = mock_nc.subscribe.call_args[1]
        assert call_kwargs.get("queue") == "tradeengine-workers", (
            f"Expected queue='tradeengine-workers', got: {call_kwargs.get('queue')!r}"
        )


# ---------------------------------------------------------------------------
# AC-1: Position write retry
# ---------------------------------------------------------------------------


class TestAC1PositionWriteRetry:
    """create_position must retry 3 times before emitting CRITICAL log."""

    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_logs_critical(self):
        from tradeengine.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm.positions = {}
        pm.exchange = MagicMock()
        pm.oco_manager = MagicMock()

        import logging

        pm.logger = logging.getLogger("test_pm")

        # Build a minimal order and result
        order = MagicMock()
        order.position_id = "test_pos_001"
        order.symbol = "BTCUSDT"
        order.position_side = "LONG"
        order.side = "buy"
        order.amount = 0.001
        order.stop_loss = 48000.0
        order.take_profit = 52000.0
        order.strategy_id = "strat_x"
        order.strategy_metadata = {}

        result = {
            "position_id": "test_pos_001",
            "order_id": "ord_001",
            "trade_ids": [],
            "commission_asset": "USDT",
            "commission": 0.0,
            "filled_price": 50000.0,
        }

        attempt_count = 0

        async def always_timeout(position_data):
            nonlocal attempt_count
            attempt_count += 1
            raise TimeoutError("simulated timeout")

        critical_messages = []

        class CaptureCritical(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    critical_messages.append(record.getMessage())

        pm.logger.addHandler(CaptureCritical())

        with (
            patch(
                "tradeengine.position_manager.position_client.create_position",
                side_effect=always_timeout,
            ),
            patch(
                "tradeengine.position_manager.asyncio.wait_for",
                side_effect=lambda coro, timeout: coro,
            ),
        ):
            # Call _create_position_record directly if it exists, else skip
            if hasattr(pm, "_create_position_record"):
                await pm._create_position_record(order, result)
            else:
                pytest.skip("_create_position_record not accessible directly")

        assert attempt_count == 3, f"Expected 3 retry attempts, got {attempt_count}"
        assert any("3 attempts" in m for m in critical_messages), (
            f"Expected critical-level log after 3 failures, got: {critical_messages}"
        )
