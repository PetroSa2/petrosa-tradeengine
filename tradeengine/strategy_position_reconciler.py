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
from datetime import datetime
from typing import TYPE_CHECKING

from shared.constants import UTC
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
    match directly.  One-way mode keys are ``(symbol, "BOTH")`` with a
    signed quantity — positive amount = LONG, negative = SHORT.
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
    if side == "LONG" and snap.quantity > 0:
        return True
    if side == "SHORT" and snap.quantity < 0:
        return True
    return False


class StrategyPositionReconciler:
    """Periodic ghost-eviction reconciler for ``StrategyPositionManager``."""

    def __init__(
        self,
        strategy_pos_manager: StrategyPositionManager,
        store: ExchangeTruthStore,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
        min_age_seconds: int = _DEFAULT_MIN_AGE_SECONDS,
    ) -> None:
        self._manager = strategy_pos_manager
        self._store = store
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

            for pos in open_positions:
                if _has_matching_exchange_position(pos, exchange_positions):
                    continue
                ghosts += 1
                age = _position_age_seconds(pos, now)
                if age < self._min_age:
                    skipped_young += 1
                    continue
                spid = pos.get("strategy_position_id")
                if not spid:
                    continue
                ok = await self._manager.evict_ghost_position(
                    strategy_position_id=spid,
                    reason="no_exchange_position",
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
