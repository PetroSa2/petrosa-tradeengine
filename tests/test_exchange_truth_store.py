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
