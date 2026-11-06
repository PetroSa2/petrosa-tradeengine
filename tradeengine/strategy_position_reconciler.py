"""Strategy-layer position reconciliation (#480).

The :class:`PositionReconciler` family already reconciles the aggregated
``PositionManager`` against Binance.  This module is its analogue for the
``StrategyPositionManager`` — the per-signal virtual tracker — and exists
because the 2026-06-18 testnet diagnosis surfaced 9 ghost strategy
positions that no exchange-layer reconciler could see.

Behaviour
---------
On each pass:

1. Pull all open strategy positions from ``StrategyPositionManager``.
2. Pull the authoritative position view from ``ExchangeTruthStore``.
3. For every strategy position whose ``(symbol, side)`` is absent from the
   exchange view AND whose ``entry_time`` is older than ``min_age_seconds``,
   call ``StrategyPositionManager.evict_ghost_position`` and increment
   ``strategy_position_ghost_evicted_total``.

#831: before evicting, the reconciler reads the symbol's trade history
(``GET /fapi/v1/userTrades`` through the injected ``fills_fetcher``) and hands
the closing fills to :func:`attribute_closing_fills`, so the closed row carries
the exchange's realized P&L, or says why it can't (``unattributed`` /
``missing``).

Read-only with respect to Binance — the reconciler never places or cancels
orders.  Its only side effect is removing already-orphaned strategy rows
from the in-memory tracker and journaling the eviction to Data Manager.

AC1: startup pass.
AC2: periodic 60s task (configurable via ``interval_seconds``).
AC4: regression test in ``tests/test_strategy_position_reconciler.py``.

The reconciler intentionally does **not** depend on ``TE_EXCHANGE_TRUTH_STORE_ENABLED``
— the flag governs *risk reads* (#459) where a wrong source can mis-size
a new trade.  Ghost eviction is purely housekeeping: if the store is
populated, ghosts are real; if not, we skip the pass (no-op).  This means
the reconciler is always-on regardless of the flag.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.constants import UTC
from tradeengine.external_close_fills import (
    attribute_closing_fills,
    missing_closure,
    position_open_ms,
)
from tradeengine.metrics import (
    strategy_position_ghost_evicted_total,
    strategy_position_ghost_gauge,
    strategy_position_reconcile_runs_total,
)

if TYPE_CHECKING:
    from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot
    from tradeengine.strategy_position_manager import StrategyPositionManager

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 60
_DEFAULT_MIN_AGE_SECONDS = 300  # 5 minutes

# (symbol, start_ms) -> the symbol's userTrades rows since start_ms.
FillsFetcher = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


def _position_age_seconds(position: dict, now: datetime) -> float:
    """Return the age of a strategy position in seconds.

    Positions without an ``entry_time`` (legacy rows) are treated as
    arbitrarily old so they are eligible for eviction.
    """
    entry_time = position.get("entry_time")
    if entry_time is None:
        return float("inf")
    if isinstance(entry_time, str):
        try:
            entry_time = datetime.fromisoformat(entry_time)
        except ValueError:
            return float("inf")
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=UTC)
    return max(0.0, (now - entry_time).total_seconds())


def _has_matching_exchange_position(
    strategy_position: dict,
    exchange_positions: dict[tuple[str, str], PositionSnapshot],
) -> bool:
    """True when the strategy position's (symbol, side) has a live exchange
    counterpart.

    Hedge mode keys are ``(symbol, "LONG")`` / ``(symbol, "SHORT")`` and
    match directly.  One-way mode keys are ``(symbol, "BOTH")``.

    #505: the one-way branch previously inferred direction from the *sign* of
    the snapshot quantity (``positive = LONG, negative = SHORT``). Live Binance
    ``positionRisk`` during the 2026-07-16 incident returned a SHORT with a
    *positive* ``positionAmt`` (9470.2), so a real short failed the
    ``snap.quantity < 0`` test and was mis-classified as a ghost — putting it at
    risk of eviction. The strategy row's explicit ``side`` is authoritative, so
    a single one-way snapshot with a non-zero magnitude matches the strategy's
    declared side regardless of the sign the live data happens to carry.
    """
    symbol = strategy_position.get("symbol")
    side = str(strategy_position.get("side", "")).upper()
    if not symbol or side not in ("LONG", "SHORT"):
        # Defensive: malformed strategy position.  Treat as ghost-eligible
        # so the reconciler can drop the bad row instead of carrying it
        # forever.
        return False

    if (symbol, side) in exchange_positions:
        return True

    snap = exchange_positions.get((symbol, "BOTH"))
    if snap is None:
        return False
    # One-way (BOTH) mode: a symbol has at most one net position, so any
    # non-zero magnitude under the (symbol, "BOTH") key is *that* position.
    # Trust the strategy row's explicit side rather than the snapshot sign,
    # which live data (see #505) has been observed to violate for SHORTs.
    return snap.quantity != 0


class StrategyPositionReconciler:
    """Periodic ghost-eviction reconciler for ``StrategyPositionManager``."""

    def __init__(
        self,
        strategy_pos_manager: StrategyPositionManager,
        store: ExchangeTruthStore,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
        min_age_seconds: int = _DEFAULT_MIN_AGE_SECONDS,
        fills_fetcher: FillsFetcher | None = None,
    ) -> None:
        self._manager = strategy_pos_manager
        self._store = store
        self._fills_fetcher = fills_fetcher
        self._interval = max(1, int(interval_seconds))
        self._min_age = max(0, int(min_age_seconds))
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running: bool = False

    @property
    def interval_seconds(self) -> int:
        return self._interval

    @property
    def min_age_seconds(self) -> int:
        return self._min_age

    async def start(self) -> None:
        """Launch the reconcile loop after running one immediate pass."""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        # AC1: startup pass before the periodic loop kicks in so ghosts
        # left over from the previous boot get cleaned up immediately.
        await self.reconcile_once()
        self._task = asyncio.create_task(
            self._loop(), name="strategy-position-reconciler"
        )
        logger.info(
            "StrategyPositionReconciler started (interval=%ds, min_age=%ds)",
            self._interval,
            self._min_age,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("StrategyPositionReconciler stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise
            if not self._running:
                break
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("StrategyPositionReconciler loop pass failed")

    async def reconcile_once(self) -> dict[str, int]:
        """Run one reconciliation pass.

        Returns a summary dict ``{open, ghosts, evicted, skipped_young}``.
        Never raises — exceptions are recorded against the runs counter.
        """
        try:
            if not getattr(self._store, "is_ready", False):
                strategy_position_reconcile_runs_total.labels(
                    result="skipped_store_not_ready"
                ).inc()
                return {"open": 0, "ghosts": 0, "evicted": 0, "skipped_young": 0}

            open_positions = self._manager.get_all_open_strategy_positions()
            exchange_positions = self._store.get_positions()
            now = datetime.now(UTC)

            ghosts = 0
            evicted = 0
            skipped_young = 0
            evictable: list[dict] = []

            for pos in open_positions:
                if _has_matching_exchange_position(pos, exchange_positions):
                    continue
                ghosts += 1
                age = _position_age_seconds(pos, now)
                if age < self._min_age:
                    skipped_young += 1
                    continue
                if not pos.get("strategy_position_id"):
                    continue
                evictable.append(pos)

            trades_by_symbol = await self._fetch_trades(evictable)

            for pos in evictable:
                spid = str(pos["strategy_position_id"])
                closure = self._closure_for(pos, trades_by_symbol, open_positions)
                ok = await self._manager.evict_ghost_position(
                    strategy_position_id=spid,
                    reason="no_exchange_position",
                    closure=closure,
                )
                if ok:
                    strategy_position_ghost_evicted_total.labels(
                        symbol=str(pos.get("symbol", "unknown")),
                        side=str(pos.get("side", "unknown")),
                        reason="no_exchange_position",
                    ).inc()
                    evicted += 1

            strategy_position_ghost_gauge.set(ghosts)
            strategy_position_reconcile_runs_total.labels(result="ok").inc()
            if ghosts:
                logger.info(
                    "StrategyPositionReconciler: open=%d ghosts=%d evicted=%d skipped_young=%d",
                    len(open_positions),
                    ghosts,
                    evicted,
                    skipped_young,
                )
            return {
                "open": len(open_positions),
                "ghosts": ghosts,
                "evicted": evicted,
                "skipped_young": skipped_young,
            }
        except Exception:
            logger.exception("StrategyPositionReconciler pass failed")
            strategy_position_reconcile_runs_total.labels(result="error").inc()
            return {"open": 0, "ghosts": 0, "evicted": 0, "skipped_young": 0}

    async def _fetch_trades(
        self, evictable: list[dict]
    ) -> dict[str, list[dict[str, Any]] | str]:
        """One userTrades read per symbol, from its earliest evictable entry.

        Values are the trade rows, or a ``missing`` reason string when the read
        could not happen or failed.
        """
        start_by_symbol: dict[str, int] = {}
        for pos in evictable:
            symbol = str(pos.get("symbol") or "")
            open_ms = position_open_ms(pos)
            if not symbol or open_ms is None:
                continue
            start_by_symbol[symbol] = min(open_ms, start_by_symbol.get(symbol, open_ms))

        result: dict[str, list[dict[str, Any]] | str] = {}
        for symbol, start_ms in start_by_symbol.items():
            if self._fills_fetcher is None:
                result[symbol] = "fetcher_unavailable"
                continue
            try:
                trades = await self._fills_fetcher(symbol, start_ms)
            except Exception as exc:
                logger.warning(
                    "StrategyPositionReconciler: userTrades read failed for %s "
                    "since %s: %s",
                    symbol,
                    start_ms,
                    exc,
                )
                result[symbol] = "fetch_failed"
                continue
            result[symbol] = trades if isinstance(trades, list) else "fetch_failed"
        return result

    @staticmethod
    def _closure_for(
        pos: dict,
        trades_by_symbol: dict[str, list[dict[str, Any]] | str],
        open_positions: list[dict],
    ) -> dict[str, Any]:
        if position_open_ms(pos) is None:
            return missing_closure("entry_time_unknown")
        trades = trades_by_symbol.get(str(pos.get("symbol") or ""))
        if trades is None:
            return missing_closure("symbol_unknown")
        if isinstance(trades, str):
            return missing_closure(trades)
        return attribute_closing_fills(pos, trades, other_positions=open_positions)
