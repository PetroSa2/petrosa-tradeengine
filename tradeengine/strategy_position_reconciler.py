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
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.constants import UTC
from tradeengine.external_close_fills import (
    FillsFetcher,
    fetch_closure,
    position_open_ms,
)
from tradeengine.metrics import (
    strategy_position_ghost_evicted_total,
    strategy_position_ghost_gauge,
    strategy_position_persisted_uncovered_gauge,
    strategy_position_reconcile_runs_total,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
        persisted_loader: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
    ) -> None:
        self._manager = strategy_pos_manager
        self._store = store
        self._fills_fetcher = fills_fetcher
        # #837: reads the persisted open rows once, on the first pass with a ready store.
        self._persisted_loader = persisted_loader
        self._hydrated = persisted_loader is None
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

    async def _hydrate_persisted_rows(self) -> None:
        """#837: load the persisted open rows a restart left out of memory, covered rows only.

        Only rows that cover the exchange's quantity on their (symbol, side), newest first (the
        re-base plan), are loaded, so the normal close paths and this reconciler see them. The rest
        are counted on ``strategy_position_persisted_uncovered`` and logged, never loaded (they
        would absorb today's exit fills) and never closed here: the audited one-shot re-base does
        that. A failed read is retried on the next pass.
        """
        from decimal import Decimal

        from tradeengine.strategy_position_rebase import plan_rebase

        assert self._persisted_loader is not None
        try:
            rows = await self._persisted_loader()
        except Exception as exc:
            logger.warning(
                "StrategyPositionReconciler: persisted rows not readable: %s", exc
            )
            return
        exchange_quantities = {
            key: Decimal(str(abs(snap.quantity)))
            for key, snap in self._store.get_positions().items()
            if snap.quantity
        }
        plans = plan_rebase(
            rows, exchange_quantities, datetime.now(UTC), grace_seconds=self._min_age
        )
        loaded = 0
        uncovered: dict[str, int] = {}
        for plan in plans:
            for entry in plan.keep:
                spid = str(entry["row"]["strategy_position_id"])
                if spid not in self._manager.strategy_positions:
                    self._manager.strategy_positions[spid] = dict(entry["row"])
                    loaded += 1
            if plan.close:
                reason = str(plan.reason)
                uncovered[reason] = uncovered.get(reason, 0) + len(plan.close)
        for reason in ("no_exchange_position", "exceeds_exchange_quantity"):
            strategy_position_persisted_uncovered_gauge.labels(reason=reason).set(
                uncovered.get(reason, 0)
            )
        self._hydrated = True
        logger.warning(
            "StrategyPositionReconciler: persisted open rows read=%d loaded=%d uncovered=%s",
            len(rows),
            loaded,
            uncovered,
        )

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

            if not self._hydrated:
                await self._hydrate_persisted_rows()

            open_positions = self._manager.get_all_open_strategy_positions()
            exchange_positions = self._store.get_positions()
            now = datetime.now(UTC)

            ghosts = 0
            evicted = 0
            skipped_young = 0
            evictable: list[dict[str, Any]] = []

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

            # Oldest first: the first read per symbol starts early enough to
            # cover every later ghost on it (fetch_closure's per-pass cache).
            evictable.sort(key=lambda p: position_open_ms(p) or 0)
            trades_cache: dict[str, tuple[int, list[dict[str, Any]] | str]] = {}

            for pos in evictable:
                spid = str(pos["strategy_position_id"])
                closure = await fetch_closure(
                    self._fills_fetcher, pos, open_positions, trades_cache
                )
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
