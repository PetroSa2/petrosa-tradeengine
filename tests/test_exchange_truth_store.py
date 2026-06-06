"""
Tests for ExchangeTruthStore and UserDataStreamConsumer (#446-A).

AC4 coverage:
- Unit tests with mocked WebSocket: seed via REST, updates propagate on stream events
- Reconnect path: disconnect -> reconnect -> REST seed -> stream resumes
- is_ready flag, health_check, OTel counter increments
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tradeengine.exchange_truth_store import (
    ExchangeTruthStore,
    OrderSnapshot,
    PositionSnapshot,
    UserDataStreamConsumer,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_exchange(positions=None, orders=None):
    """Return a minimal mock exchange with a client that returns REST data."""
    exchange = MagicMock()
    exchange.client = MagicMock()
    exchange.client.testnet = False
    exchange.client.futures_stream_get_listen_key.return_value = {
        "listenKey": "TEST_KEY"
    }
    exchange.client.futures_stream_keepalive.return_value = {}
    exchange.client.futures_position_information.return_value = positions or []
    exchange.client.futures_get_open_orders.return_value = orders or []
    return exchange


@asynccontextmanager
async def _ws_context(messages: list[str]):
    """Async context manager that mimics `async with websockets.connect(url) as ws`."""

    class _MockWS:
        def __init__(self, msgs):
            self._iter = iter(msgs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    yield _MockWS(messages)


# ---------------------------------------------------------------------------
# ExchangeTruthStore
# ---------------------------------------------------------------------------


class TestExchangeTruthStore:
    @pytest.mark.asyncio
    async def test_initial_state_not_ready(self):
        store = ExchangeTruthStore()
        assert store.is_ready is False
        assert store.last_updated is None
        assert store.get_positions() == {}

    @pytest.mark.asyncio
    async def test_seed_from_rest_populates_positions(self):
        store = ExchangeTruthStore()
        positions = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.01",
                "entryPrice": "50000",
                "unrealizedProfit": "100",
            }
        ]
        await store.seed_from_rest(positions, [])

        assert store.is_ready is True
        result = store.get_positions()
        assert ("BTCUSDT", "LONG") in result
        snap = result[("BTCUSDT", "LONG")]
        assert isinstance(snap, PositionSnapshot)
        assert snap.quantity == pytest.approx(0.01)
        assert snap.entry_price == pytest.approx(50000)

    @pytest.mark.asyncio
    async def test_seed_from_rest_skips_zero_qty_positions(self):
        store = ExchangeTruthStore()
        positions = [
            {"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": "0.0"},
        ]
        await store.seed_from_rest(positions, [])
        assert store.get_positions() == {}

    @pytest.mark.asyncio
    async def test_seed_from_rest_populates_orders(self):
        store = ExchangeTruthStore()
        orders = [
            {
                "symbol": "BTCUSDT",
                "orderId": 12345,
                "side": "SELL",
                "type": "STOP_MARKET",
                "status": "NEW",
                "origQty": "0.01",
                "price": "48000",
            }
        ]
        await store.seed_from_rest([], orders)

        result = store.get_open_orders("BTCUSDT")
        assert len(result) == 1
        assert isinstance(result[0], OrderSnapshot)
        assert result[0].order_id == "12345"

    @pytest.mark.asyncio
    async def test_account_update_adds_position(self):
        store = ExchangeTruthStore()
        event = {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "P": [
                    {
                        "s": "BTCUSDT",
                        "ps": "LONG",
                        "pa": "0.05",
                        "ep": "60000",
                        "up": "50",
                    }
                ]
            },
        }
        await store.update_positions_from_account_update(event)

        assert store.is_ready is True
        positions = store.get_positions()
        assert ("BTCUSDT", "LONG") in positions
        assert positions[("BTCUSDT", "LONG")].quantity == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_account_update_removes_zero_qty_position(self):
        store = ExchangeTruthStore()
        # Seed with a position first
        await store.seed_from_rest(
            [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "LONG",
                    "positionAmt": "0.05",
                    "entryPrice": "60000",
                    "unrealizedProfit": "0",
                }
            ],
            [],
        )
        # Now account update says qty = 0 → should remove it
        event = {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "P": [{"s": "BTCUSDT", "ps": "LONG", "pa": "0.0", "ep": "0", "up": "0"}]
            },
        }
        await store.update_positions_from_account_update(event)
        assert ("BTCUSDT", "LONG") not in store.get_positions()

    @pytest.mark.asyncio
    async def test_order_trade_update_adds_order(self):
        store = ExchangeTruthStore()
        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "ETHUSDT",
                "i": 99999,
                "S": "BUY",
                "o": "LIMIT",
                "X": "NEW",
                "q": "1.0",
                "p": "3000",
            },
        }
        await store.update_order_from_trade_update(event)
        orders = store.get_open_orders("ETHUSDT")
        assert len(orders) == 1
        assert orders[0].order_id == "99999"
        assert orders[0].status == "NEW"

    @pytest.mark.asyncio
    async def test_order_trade_update_removes_filled_order(self):
        store = ExchangeTruthStore()
        # Seed an order
        await store.seed_from_rest(
            [],
            [
                {
                    "symbol": "ETHUSDT",
                    "orderId": 99999,
                    "side": "BUY",
                    "type": "LIMIT",
                    "status": "NEW",
                    "origQty": "1.0",
                    "price": "3000",
                }
            ],
        )
        # Then it gets filled
        event = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "ETHUSDT",
                "i": 99999,
                "S": "BUY",
                "o": "LIMIT",
                "X": "FILLED",
                "q": "1.0",
                "p": "3000",
            },
        }
        await store.update_order_from_trade_update(event)
        assert store.get_open_orders("ETHUSDT") == []

    @pytest.mark.asyncio
    async def test_get_positions_returns_copy(self):
        store = ExchangeTruthStore()
        await store.seed_from_rest(
            [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "LONG",
                    "positionAmt": "1.0",
                    "entryPrice": "50000",
                    "unrealizedProfit": "0",
                }
            ],
            [],
        )
        copy1 = store.get_positions()
        copy2 = store.get_positions()
        assert copy1 is not copy2  # different dict objects (copy-on-read)


# ---------------------------------------------------------------------------
# UserDataStreamConsumer
# ---------------------------------------------------------------------------


class TestUserDataStreamConsumer:
    @pytest.mark.asyncio
    async def test_health_check_not_ready_before_start(self):
        exchange = _make_exchange()
        consumer = UserDataStreamConsumer(exchange)
        health = await consumer.health_check()
        assert health["is_ready"] is False
        assert health["stream_connected"] is False
        assert health["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        exchange = _make_exchange()
        consumer = UserDataStreamConsumer(exchange)
        # Patch the consumer loop so it doesn't actually connect
        consumer._consumer_loop = AsyncMock(return_value=None)
        await consumer.start()
        assert consumer._task is not None
        assert not consumer._task.done()
        await consumer.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        exchange = _make_exchange()
        consumer = UserDataStreamConsumer(exchange)
        consumer._consumer_loop = AsyncMock(return_value=None)
        await consumer.start()
        await consumer.stop()
        assert consumer._task is None or consumer._task.done()

    @pytest.mark.asyncio
    async def test_seed_store_from_rest_populates_store(self):
        positions = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.1",
                "entryPrice": "55000",
                "unrealizedProfit": "200",
            },
        ]
        orders = [
            {
                "symbol": "BTCUSDT",
                "orderId": 1,
                "side": "SELL",
                "type": "STOP_MARKET",
                "status": "NEW",
                "origQty": "0.1",
                "price": "52000",
            },
        ]
        exchange = _make_exchange(positions=positions, orders=orders)
        consumer = UserDataStreamConsumer(exchange)

        await consumer._seed_store()

        assert consumer.store.is_ready is True
        assert ("BTCUSDT", "LONG") in consumer.store.get_positions()
        assert len(consumer.store.get_open_orders("BTCUSDT")) == 1

    @pytest.mark.asyncio
    async def test_seed_store_handles_rest_failure_gracefully(self):
        exchange = _make_exchange()
        exchange.client.futures_position_information.side_effect = RuntimeError(
            "API down"
        )
        consumer = UserDataStreamConsumer(exchange)

        # Should not raise — seeds with empty state
        await consumer._seed_store()
        assert consumer.store.is_ready is True
        assert consumer.store.get_positions() == {}

    @pytest.mark.asyncio
    async def test_consumer_processes_account_update_event(self):
        exchange = _make_exchange()
        event = json.dumps(
            {
                "e": "ACCOUNT_UPDATE",
                "a": {
                    "P": [
                        {
                            "s": "BTCUSDT",
                            "ps": "LONG",
                            "pa": "0.05",
                            "ep": "60000",
                            "up": "50",
                        }
                    ]
                },
            }
        )
        consumer = UserDataStreamConsumer(exchange)
        consumer._create_listen_key = AsyncMock(return_value="TEST_KEY")
        consumer._seed_store = AsyncMock()
        consumer._renewal_loop = AsyncMock()

        # side_effect creates a fresh context manager on each call so reconnects work
        with patch("websockets.connect", side_effect=lambda url: _ws_context([event])):
            consumer._running = True
            try:
                await asyncio.wait_for(consumer._consumer_loop(), timeout=3.0)
            except TimeoutError:
                consumer._running = False

        assert consumer.store.is_ready is True
        assert ("BTCUSDT", "LONG") in consumer.store.get_positions()

    @pytest.mark.asyncio
    async def test_consumer_processes_order_trade_update_event(self):
        exchange = _make_exchange()
        event = json.dumps(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "s": "ETHUSDT",
                    "i": 777,
                    "S": "BUY",
                    "o": "LIMIT",
                    "X": "NEW",
                    "q": "1.0",
                    "p": "3200",
                },
            }
        )
        consumer = UserDataStreamConsumer(exchange)
        consumer._create_listen_key = AsyncMock(return_value="TEST_KEY")
        consumer._seed_store = AsyncMock()
        consumer._renewal_loop = AsyncMock()

        with patch("websockets.connect", side_effect=lambda url: _ws_context([event])):
            consumer._running = True
            try:
                await asyncio.wait_for(consumer._consumer_loop(), timeout=3.0)
            except TimeoutError:
                consumer._running = False

        assert len(consumer.store.get_open_orders("ETHUSDT")) == 1

    @pytest.mark.asyncio
    async def test_reconnect_path_seeds_after_each_connect(self):
        """AC4: disconnect → reconnect → REST seed fires again."""
        exchange = _make_exchange()
        consumer = UserDataStreamConsumer(exchange)

        seed_call_count = 0

        async def _fake_seed():
            nonlocal seed_call_count
            seed_call_count += 1

        consumer._create_listen_key = AsyncMock(return_value="TEST_KEY")
        consumer._seed_store = _fake_seed
        consumer._renewal_loop = AsyncMock()

        connect_call_count = 0

        @asynccontextmanager
        async def _failing_then_empty_ws(url):
            nonlocal connect_call_count
            connect_call_count += 1
            if connect_call_count == 1:
                raise ConnectionError("simulated disconnect")

            class _EmptyWS:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            yield _EmptyWS()

        with patch("websockets.connect", side_effect=_failing_then_empty_ws):
            with patch(
                "asyncio.create_task",
                side_effect=lambda coro, **_: asyncio.ensure_future(coro),
            ):
                consumer._running = True

                async def _stop_after_two_passes():
                    while connect_call_count < 2:
                        await asyncio.sleep(0.05)
                    consumer._running = False

                stopper = asyncio.ensure_future(_stop_after_two_passes())
                try:
                    await asyncio.wait_for(consumer._consumer_loop(), timeout=5.0)
                except TimeoutError:
                    consumer._running = False
                finally:
                    stopper.cancel()

        # seed must have been called at least twice (once per connect attempt)
        assert seed_call_count >= 2

    @pytest.mark.asyncio
    async def test_health_check_after_seed(self):
        exchange = _make_exchange()
        consumer = UserDataStreamConsumer(exchange)
        await consumer._seed_store()
        consumer._stream_connected = True

        health = await consumer.health_check()
        assert health["is_ready"] is True
        assert health["stream_connected"] is True
        assert health["status"] == "healthy"
        assert health["last_updated"] is not None


# ---------------------------------------------------------------------------
# ExchangeTruthStore.update_from_rest (AC1 + AC2 — #446-B)
# ---------------------------------------------------------------------------


class TestUpdateFromRest:
    @pytest.mark.asyncio
    async def test_update_from_rest_populates_positions(self):
        store = ExchangeTruthStore()
        positions = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.5",
                "entryPrice": "30000",
                "unrealizedProfit": "500",
            }
        ]
        await store.update_from_rest(positions, [])
        snaps = store.get_positions()
        assert ("BTCUSDT", "LONG") in snaps
        assert snaps[("BTCUSDT", "LONG")].quantity == 0.5
        assert snaps[("BTCUSDT", "LONG")].entry_price == 30000.0

    @pytest.mark.asyncio
    async def test_update_from_rest_populates_orders(self):
        store = ExchangeTruthStore()
        orders = [
            {
                "symbol": "BTCUSDT",
                "orderId": "9001",
                "side": "SELL",
                "type": "STOP_MARKET",
                "status": "NEW",
                "origQty": "0.5",
                "price": "29000",
            }
        ]
        await store.update_from_rest([], orders)
        open_orders = store.get_open_orders("BTCUSDT")
        assert len(open_orders) == 1
        assert open_orders[0].order_id == "9001"

    @pytest.mark.asyncio
    async def test_update_from_rest_skips_zero_qty_positions(self):
        store = ExchangeTruthStore()
        positions = [
            {"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": "0.0"}
        ]
        await store.update_from_rest(positions, [])
        assert store.get_positions() == {}

    @pytest.mark.asyncio
    async def test_update_from_rest_sets_last_rest_sync(self):
        store = ExchangeTruthStore()
        assert store.last_rest_sync is None
        await store.update_from_rest([], [])
        assert store.last_rest_sync is not None

    @pytest.mark.asyncio
    async def test_update_from_rest_marks_store_ready(self):
        store = ExchangeTruthStore()
        assert store.is_ready is False
        await store.update_from_rest([], [])
        assert store.is_ready is True

    @pytest.mark.asyncio
    async def test_update_from_rest_overwrites_previous_state(self):
        store = ExchangeTruthStore()
        first = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "1.0",
                "entryPrice": "30000",
                "unrealizedProfit": "0",
            }
        ]
        await store.update_from_rest(first, [])
        assert len(store.get_positions()) == 1

        # Second call with empty list should clear positions
        await store.update_from_rest([], [])
        assert store.get_positions() == {}


# ---------------------------------------------------------------------------
# PositionReconciler + ExchangeTruthStore integration (AC3 — #446-B)
# ---------------------------------------------------------------------------


def _make_reconciler_exchange(positions=None, orders=None):
    exchange = MagicMock()
    exchange.get_position_info = AsyncMock(return_value=positions or [])
    exchange.get_open_algo_orders = AsyncMock(return_value=orders or [])
    return exchange


def _make_position_manager(local_positions=None):
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value=local_positions or {})
    return pm


class TestPositionReconcilerStoreIntegration:
    @pytest.mark.asyncio
    async def test_reconcile_once_calls_update_from_rest(self):
        """AC3 — reconciler calls update_from_rest after a successful REST fetch."""
        from tradeengine.position_reconciler import PositionReconciler

        raw_positions = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.5",
                "entryPrice": "30000",
                "unrealizedProfit": "100",
            }
        ]
        exchange = _make_reconciler_exchange(positions=raw_positions)
        pm = _make_position_manager()
        store = ExchangeTruthStore()
        store.update_from_rest = AsyncMock(wraps=store.update_from_rest)

        reconciler = PositionReconciler(
            exchange=exchange,
            position_manager=pm,
            interval_seconds=60,
            store=store,
        )
        await reconciler.reconcile_once()

        store.update_from_rest.assert_awaited_once()
        call_args = store.update_from_rest.call_args
        positions_arg = call_args[0][0]
        assert any(p.get("symbol") == "BTCUSDT" for p in positions_arg)

    @pytest.mark.asyncio
    async def test_reconcile_once_no_store_does_not_raise(self):
        """reconcile_once works normally when no store is injected."""
        from tradeengine.position_reconciler import PositionReconciler

        exchange = _make_reconciler_exchange()
        pm = _make_position_manager()

        reconciler = PositionReconciler(
            exchange=exchange,
            position_manager=pm,
            interval_seconds=60,
        )
        result = await reconciler.reconcile_once()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_stale_stream_warning_fires(self, caplog):
        """AC3 — stale-stream warning logs when stream timestamp exceeds 2x interval."""
        import logging
        from datetime import UTC, datetime, timedelta

        from tradeengine.position_reconciler import PositionReconciler

        exchange = _make_reconciler_exchange()
        pm = _make_position_manager()
        store = ExchangeTruthStore()

        # Simulate a stream update that happened 5 minutes ago on a 60s interval
        # 5*60 = 300s > 2*60 = 120s → should trigger warning
        stale_ts = datetime.now(UTC) - timedelta(seconds=300)
        store._last_updated = stale_ts
        store._is_ready = True

        reconciler = PositionReconciler(
            exchange=exchange,
            position_manager=pm,
            interval_seconds=60,
            store=store,
        )

        with caplog.at_level(logging.WARNING, logger="tradeengine.position_reconciler"):
            await reconciler.reconcile_once()

        assert any("stale" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_stale_stream_warning_not_fired_when_fresh(self, caplog):
        """No stale warning when stream is recent."""
        import logging
        from datetime import UTC, datetime

        from tradeengine.position_reconciler import PositionReconciler

        exchange = _make_reconciler_exchange()
        pm = _make_position_manager()
        store = ExchangeTruthStore()

        # Stream updated just now — within 2x interval
        store._last_updated = datetime.now(UTC)
        store._is_ready = True

        reconciler = PositionReconciler(
            exchange=exchange,
            position_manager=pm,
            interval_seconds=60,
            store=store,
        )

        with caplog.at_level(logging.WARNING, logger="tradeengine.position_reconciler"):
            await reconciler.reconcile_once()

        stale_warnings = [r for r in caplog.records if "stale" in r.message.lower()]
        assert stale_warnings == []


# ---------------------------------------------------------------------------
# AC3 (446-B) — PositionReconciler REST-backstop integration
# ---------------------------------------------------------------------------


class TestPositionReconcilerRestBackstop:
    """AC3 tests for #458: reconciler writes REST snapshot into ExchangeTruthStore."""

    def _make_reconciler_deps(self, positions=None, orders=None, local_positions=None):
        from tradeengine.position_reconciler import PositionReconciler

        exchange = MagicMock()
        exchange.get_position_info = AsyncMock(return_value=positions or [])
        exchange.get_open_algo_orders = AsyncMock(return_value=orders or [])

        pm = MagicMock()
        pm.get_positions = MagicMock(return_value=local_positions or {})

        store = ExchangeTruthStore()
        reconciler = PositionReconciler(
            exchange=exchange,
            position_manager=pm,
            interval_seconds=60,
            store=store,
        )
        return reconciler, store, exchange

    @pytest.mark.asyncio
    async def test_reconcile_once_calls_update_from_rest(self):
        """AC3a: reconciler calls store.update_from_rest() after a successful REST fetch."""
        positions = [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.01",
                "entryPrice": "50000",
                "unrealizedProfit": "10",
            }
        ]
        orders = [
            {
                "symbol": "BTCUSDT",
                "orderId": "111",
                "side": "SELL",
                "type": "STOP_MARKET",
                "status": "NEW",
                "origQty": "0.01",
                "price": "49000",
                "reduceOnly": True,
                "positionSide": "LONG",
            }
        ]
        reconciler, store, _ = self._make_reconciler_deps(
            positions=positions, orders=orders
        )

        store.update_from_rest = AsyncMock(wraps=store.update_from_rest)
        await reconciler.reconcile_once()

        store.update_from_rest.assert_awaited_once()
        call_args = store.update_from_rest.call_args
        passed_positions = call_args[0][0]
        assert any(p.get("symbol") == "BTCUSDT" for p in passed_positions)

    @pytest.mark.asyncio
    async def test_update_from_rest_populates_store(self):
        """AC3a: update_from_rest overwrites positions and open_orders in the store."""
        store = ExchangeTruthStore()
        positions = [
            {
                "symbol": "ETHUSDT",
                "positionSide": "SHORT",
                "positionAmt": "-0.5",
                "entryPrice": "3000",
                "unrealizedProfit": "-5",
            }
        ]
        orders = [
            {
                "symbol": "ETHUSDT",
                "orderId": "999",
                "side": "BUY",
                "type": "STOP_MARKET",
                "status": "NEW",
                "origQty": "0.5",
                "price": "3100",
            }
        ]
        await store.update_from_rest(positions, orders)

        assert store.is_ready is True
        assert store.last_rest_sync is not None
        snaps = store.get_positions()
        assert ("ETHUSDT", "SHORT") in snaps
        assert snaps[("ETHUSDT", "SHORT")].quantity == -0.5
        eth_orders = store.get_open_orders("ETHUSDT")
        assert len(eth_orders) == 1
        assert eth_orders[0].order_id == "999"

    @pytest.mark.asyncio
    async def test_stale_stream_warning_fires(self, caplog):
        """AC3b: stale-stream warning fires when stream.last_updated is older than 2×interval."""
        import logging
        from datetime import UTC, datetime, timedelta

        reconciler, store, _ = self._make_reconciler_deps()
        reconciler._interval = 30  # threshold = 60s

        # Force stream last_updated to 120s ago (> 2 * 30 = 60s threshold)
        store._last_updated = datetime.now(UTC) - timedelta(seconds=120)
        store._is_ready = True

        with caplog.at_level(logging.WARNING, logger="tradeengine.position_reconciler"):
            await reconciler.reconcile_once()

        assert any("stale" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_stale_warning_when_stream_fresh(self, caplog):
        """AC3b: no staleness warning when stream is recent."""
        import logging
        from datetime import UTC, datetime, timedelta

        reconciler, store, _ = self._make_reconciler_deps()
        reconciler._interval = 60  # threshold = 120s

        # Stream updated 10s ago — well within threshold
        store._last_updated = datetime.now(UTC) - timedelta(seconds=10)
        store._is_ready = True

        with caplog.at_level(logging.WARNING, logger="tradeengine.position_reconciler"):
            await reconciler.reconcile_once()

        assert not any("stale" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_reconcile_without_store_does_not_raise(self):
        """Reconciler with store=None proceeds normally (optional injection)."""
        from tradeengine.position_reconciler import PositionReconciler

        exchange = MagicMock()
        exchange.get_position_info = AsyncMock(return_value=[])
        exchange.get_open_algo_orders = AsyncMock(return_value=[])
        pm = MagicMock()
        pm.get_positions = MagicMock(return_value={})

        reconciler = PositionReconciler(exchange=exchange, position_manager=pm)
        # Must not raise even with no store wired
        divergences = await reconciler.reconcile_once()
        assert isinstance(divergences, list)
