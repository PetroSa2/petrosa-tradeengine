"""
Exchange Truth Store (#446-A)

Real-time authoritative view of Binance Futures positions and open orders,
maintained via the user-data WebSocket stream.  Local DB is authoritative
for engine intentions; the exchange is authoritative for position existence,
qty, side, and open protective orders.

AC1: UserDataStreamConsumer — WS stream, ACCOUNT_UPDATE, ORDER_TRADE_UPDATE,
     listen-key renewal, exponential-backoff reconnect + REST seed.
AC2: ExchangeTruthStore — thread/async-safe interface.
AC3: start()/stop() lifecycle, health_check(), OTel counter.
AC4: unit tests in tests/test_exchange_truth_store.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from typing import TYPE_CHECKING, Any

from prometheus_client import Counter, Gauge

if TYPE_CHECKING:
    from tradeengine.exchange.binance import BinanceFuturesExchange

logger = logging.getLogger(__name__)

# AC3 — OTel counter (event_type: account_update | order_trade_update | reconnect | rest_seed)
exchange_truth_store_events_total = Counter(
    "tradeengine_exchange_truth_store_events_total",
    "Total user-data stream events processed by ExchangeTruthStore",
    ["event_type"],
)

# AC2 (446-B) — seconds since last WebSocket stream update, set on each REST reconcile pass
exchange_truth_store_stale_seconds = Gauge(
    "tradeengine_exchange_truth_store_stale_seconds",
    "Seconds since the last WebSocket stream update (measured on each REST reconcile pass)",
)

_LISTEN_KEY_RENEWAL_SECS = 55 * 60  # Binance expires keys at 60 min
_RECONNECT_BASE_DELAY = 2.0
_RECONNECT_MAX_DELAY = 60.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PositionSnapshot:
    symbol: str
    side: str  # "LONG" | "SHORT" | "BOTH"
    quantity: float
    entry_price: float
    unrealized_pnl: float
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class OrderSnapshot:
    symbol: str
    order_id: str
    side: str
    order_type: str
    status: str
    quantity: float
    price: float
    position_side: str = "BOTH"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# ExchangeTruthStore (AC2)
# ---------------------------------------------------------------------------


class ExchangeTruthStore:
    """
    Async-safe in-memory snapshot of exchange positions and open orders.

    Reads return shallow copies so callers are never blocked while the
    stream task holds the lock.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._positions: dict[tuple[str, str], PositionSnapshot] = {}
        # keyed by (symbol, order_id)
        self._open_orders: dict[tuple[str, str], OrderSnapshot] = {}
        self._last_updated: datetime | None = None
        self._last_rest_sync: datetime | None = None
        self._is_ready: bool = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @property
    def last_updated(self) -> datetime | None:
        return self._last_updated

    @property
    def last_rest_sync(self) -> datetime | None:
        return self._last_rest_sync

    def get_positions(self) -> dict[tuple[str, str], PositionSnapshot]:
        return dict(self._positions)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        return [o for (sym, _), o in self._open_orders.items() if sym == symbol]

    async def update_positions_from_account_update(self, event: dict[str, Any]) -> None:
        """Apply an ACCOUNT_UPDATE WS event to the positions snapshot."""
        positions = event.get("a", {}).get("P", []) or event.get("P", [])
        async with self._lock:
            for p in positions:
                symbol = p.get("s", "")
                side = p.get("ps", "BOTH").upper()
                qty = float(p.get("pa", 0))
                entry = float(p.get("ep", 0))
                upnl = float(p.get("up", 0))
                if abs(qty) < 1e-9:
                    self._positions.pop((symbol, side), None)
                else:
                    self._positions[(symbol, side)] = PositionSnapshot(
                        symbol=symbol,
                        side=side,
                        quantity=qty,
                        entry_price=entry,
                        unrealized_pnl=upnl,
                    )
            self._last_updated = datetime.now(UTC)
            self._is_ready = True

    async def update_order_from_trade_update(self, event: dict[str, Any]) -> None:
        """Apply an ORDER_TRADE_UPDATE WS event to the open-orders snapshot."""
        o = event.get("o", event)
        symbol = o.get("s", "")
        order_id = str(o.get("i", ""))
        status = o.get("X", "")
        async with self._lock:
            if status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                self._open_orders.pop((symbol, order_id), None)
            else:
                self._open_orders[(symbol, order_id)] = OrderSnapshot(
                    symbol=symbol,
                    order_id=order_id,
                    side=o.get("S", ""),
                    order_type=o.get("o", ""),
                    status=status,
                    quantity=float(o.get("q", 0)),
                    price=float(o.get("p", 0)),
                    position_side=o.get("ps", "BOTH").upper(),
                )
            self._last_updated = datetime.now(UTC)
            self._is_ready = True

    async def seed_from_rest(
        self,
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
    ) -> None:
        """Overwrite store with REST snapshot (used on connect/reconnect)."""
        async with self._lock:
            self._positions.clear()
            for p in positions:
                symbol = p.get("symbol", "")
                side = p.get("positionSide", "BOTH").upper()
                qty = float(p.get("positionAmt", 0))
                if abs(qty) < 1e-9:
                    continue
                self._positions[(symbol, side)] = PositionSnapshot(
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedProfit", 0)),
                )
            self._open_orders.clear()
            for o in orders:
                symbol = o.get("symbol", "")
                order_id = str(o.get("orderId", ""))
                self._open_orders[(symbol, order_id)] = OrderSnapshot(
                    symbol=symbol,
                    order_id=order_id,
                    side=o.get("side", ""),
                    order_type=o.get("type", ""),
                    status=o.get("status", ""),
                    quantity=float(o.get("origQty", 0)),
                    price=float(o.get("price", 0)),
                )
            self._last_updated = datetime.now(UTC)
            self._is_ready = True

    async def update_from_rest(
        self,
        positions: list[dict[str, Any]],
        orders: list[dict[str, Any]],
    ) -> None:
        """Overwrite store with REST snapshot from PositionReconciler (AC1 — 446-B).

        Called after each reconcile_once() pass.  REST is authoritative: this
        snapshot replaces stream-derived state for all currently-known symbols.
        """
        async with self._lock:
            self._positions.clear()
            for p in positions:
                symbol = p.get("symbol", "")
                side = p.get("positionSide", "BOTH").upper()
                qty = float(p.get("positionAmt", 0))
                if abs(qty) < 1e-9:
                    continue
                self._positions[(symbol, side)] = PositionSnapshot(
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedProfit", 0)),
                )
            self._open_orders.clear()
            for o in orders:
                symbol = o.get("symbol", "")
                order_id = str(o.get("orderId", o.get("i", "")))
                self._open_orders[(symbol, order_id)] = OrderSnapshot(
                    symbol=symbol,
                    order_id=order_id,
                    side=o.get("side", o.get("S", "")),
                    order_type=o.get("type", o.get("o", "")),
                    status=o.get("status", o.get("X", "")),
                    quantity=float(o.get("origQty", o.get("q", 0))),
                    price=float(o.get("price", o.get("p", 0))),
                )
            self._last_rest_sync = datetime.now(UTC)
            self._is_ready = True


# ---------------------------------------------------------------------------
# UserDataStreamConsumer (AC1 + AC3)
# ---------------------------------------------------------------------------


class UserDataStreamConsumer:
    """
    Binance Futures user-data WebSocket stream consumer.

    Maintains ExchangeTruthStore with real-time position/order state.
    Reconnects with exponential backoff; seeds from REST on each connect.
    """

    _WS_MAINNET_URL = "wss://fstream.binance.com/ws"
    _WS_TESTNET_URL = "wss://stream.binancefuture.com/ws"

    def __init__(
        self,
        exchange: BinanceFuturesExchange,
        store: ExchangeTruthStore | None = None,
    ) -> None:
        self._exchange = exchange
        self.store = store or ExchangeTruthStore()
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._renewal_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._listen_key: str | None = None
        self._stream_connected: bool = False
        self._running: bool = False

    # ------------------------------------------------------------------
    # AC3 — lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(
            self._consumer_loop(), name="user-data-stream-consumer"
        )
        logger.info("UserDataStreamConsumer started")

    async def stop(self) -> None:
        self._running = False
        for t in (self._task, self._renewal_task):
            if t is not None and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._renewal_task = None
        self._stream_connected = False
        logger.info("UserDataStreamConsumer stopped")

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": (
                "healthy"
                if self.store.is_ready and self._stream_connected
                else "degraded"
            ),
            "last_updated": (
                self.store.last_updated.isoformat() if self.store.last_updated else None
            ),
            "is_ready": self.store.is_ready,
            "stream_connected": self._stream_connected,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_listen_key(self) -> str:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._exchange.client.futures_stream_get_listen_key(),
        )
        key = result.get("listenKey", "") if isinstance(result, dict) else str(result)
        if not key:
            raise RuntimeError("Empty listen key from Binance")
        self._listen_key = key
        return key

    async def _renew_listen_key(self, key: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._exchange.client.futures_stream_keepalive(listenKey=key),
        )

    async def _seed_store(self) -> None:
        """Seed the store from REST (positions + open orders) before resuming stream."""
        loop = asyncio.get_event_loop()
        try:
            positions = await loop.run_in_executor(
                None,
                lambda: self._exchange.client.futures_position_information(),
            )
            orders = await loop.run_in_executor(
                None,
                lambda: self._exchange.client.futures_get_open_orders(),
            )
        except Exception:
            logger.exception(
                "UserDataStreamConsumer: REST seed failed, using empty state"
            )
            positions = []
            orders = []
        await self.store.seed_from_rest(positions or [], orders or [])
        exchange_truth_store_events_total.labels(event_type="rest_seed").inc()

    async def _renewal_loop(self, key: str) -> None:
        """Keepalive loop — renews the listen key every 55 minutes."""
        while self._running:
            await asyncio.sleep(_LISTEN_KEY_RENEWAL_SECS)
            try:
                await self._renew_listen_key(key)
                logger.debug("UserDataStreamConsumer: listen key renewed")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("UserDataStreamConsumer: listen key renewal failed")

    async def _consumer_loop(self) -> None:
        """Main loop: connect, seed, stream, reconnect with backoff on failure."""
        import websockets  # deferred import so tests can patch easily

        is_testnet = bool(
            getattr(getattr(self._exchange, "client", None), "testnet", False)
        )
        ws_base = self._WS_TESTNET_URL if is_testnet else self._WS_MAINNET_URL
        delay = _RECONNECT_BASE_DELAY

        while self._running:
            try:
                key = await self._create_listen_key()
                url = f"{ws_base}/{key}"

                if self._renewal_task and not self._renewal_task.done():
                    self._renewal_task.cancel()
                    try:
                        await self._renewal_task
                    except asyncio.CancelledError:
                        pass
                self._renewal_task = asyncio.create_task(
                    self._renewal_loop(key), name="listen-key-renewal"
                )

                await self._seed_store()

                async with websockets.connect(url) as ws:
                    self._stream_connected = True
                    logger.info(
                        "UserDataStreamConsumer: connected (testnet=%s)", is_testnet
                    )
                    delay = _RECONNECT_BASE_DELAY  # reset on clean connect

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            logger.warning(
                                "UserDataStreamConsumer: invalid JSON payload"
                            )
                            continue

                        event_type = event.get("e", "")
                        if event_type == "ACCOUNT_UPDATE":
                            await self.store.update_positions_from_account_update(event)
                            exchange_truth_store_events_total.labels(
                                event_type="account_update"
                            ).inc()
                        elif event_type == "ORDER_TRADE_UPDATE":
                            await self.store.update_order_from_trade_update(event)
                            exchange_truth_store_events_total.labels(
                                event_type="order_trade_update"
                            ).inc()
                        else:
                            logger.debug(
                                "UserDataStreamConsumer: ignoring event_type=%s",
                                event_type,
                            )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "UserDataStreamConsumer: stream error, reconnecting in %.1fs", delay
                )
                exchange_truth_store_events_total.labels(event_type="reconnect").inc()
            finally:
                self._stream_connected = False

            if not self._running:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)
