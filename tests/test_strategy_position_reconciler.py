"""Tests for tradeengine.strategy_position_reconciler (#480).

Covers AC1 (startup pass), AC2 (periodic loop), AC4 (regression — ghost
row seeded, reconciler called, row evicted within one cycle).  AC5
(operator-facing total_checked == /positions total) is exercised via the
property test at the bottom that asserts the reconciler leaves no
strategy position whose (symbol, side) is absent from the truth store.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot
from tradeengine.strategy_position_manager import StrategyPositionManager
from tradeengine.strategy_position_reconciler import (
    StrategyPositionReconciler,
    _has_matching_exchange_position,
    _position_age_seconds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spos(
    spid: str,
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    entry_time: datetime | None = None,
    status: str = "open",
) -> dict:
    return {
        "strategy_position_id": spid,
        "strategy_id": "strat-1",
        "symbol": symbol,
        "side": side,
        "entry_quantity": 0.01,
        "entry_price": 50_000.0,
        "entry_time": entry_time or datetime.now(UTC) - timedelta(hours=1),
        "status": status,
        "exchange_position_key": f"{symbol}_{side}",
        "sl_order_id": None,
        "tp_order_id": None,
    }


async def _build_store(
    positions: dict[tuple[str, str], PositionSnapshot] | None = None,
    is_ready: bool = True,
) -> ExchangeTruthStore:
    store = ExchangeTruthStore()
    # Use update_from_rest to avoid touching the streaming lock semantics
    # while still flipping _is_ready and seeding the snapshot.
    rest_positions = []
    if positions:
        for (sym, side), snap in positions.items():
            rest_positions.append(
                {
                    "symbol": sym,
                    "positionSide": side,
                    "positionAmt": str(snap.quantity),
                    "entryPrice": str(snap.entry_price),
                    "unrealizedProfit": str(snap.unrealized_pnl),
                }
            )
    await store.update_from_rest(rest_positions, [])
    if not is_ready:
        # update_from_rest forces is_ready=True; expose the cold-store
        # path by resetting the flag here.
        store._is_ready = False
    return store


def _manager_with(*positions: dict) -> StrategyPositionManager:
    mgr = StrategyPositionManager()
    for pos in positions:
        mgr.strategy_positions[pos["strategy_position_id"]] = pos
    return mgr


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_matching_hedge_mode_long(self) -> None:
        snap = PositionSnapshot(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        assert _has_matching_exchange_position(_spos("a"), {("BTCUSDT", "LONG"): snap})

    def test_no_match_when_side_differs(self) -> None:
        snap = PositionSnapshot(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        assert not _has_matching_exchange_position(
            _spos("a", side="SHORT"), {("BTCUSDT", "LONG"): snap}
        )

    def test_one_way_mode_long_via_positive_qty(self) -> None:
        snap = PositionSnapshot(
            symbol="BTCUSDT",
            side="BOTH",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        assert _has_matching_exchange_position(
            _spos("a", side="LONG"), {("BTCUSDT", "BOTH"): snap}
        )

    def test_one_way_mode_short_via_negative_qty(self) -> None:
        snap = PositionSnapshot(
            symbol="BTCUSDT",
            side="BOTH",
            quantity=-0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        assert _has_matching_exchange_position(
            _spos("a", side="SHORT"), {("BTCUSDT", "BOTH"): snap}
        )

    def test_one_way_mode_wrong_direction_is_ghost(self) -> None:
        snap = PositionSnapshot(
            symbol="BTCUSDT",
            side="BOTH",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        assert not _has_matching_exchange_position(
            _spos("a", side="SHORT"), {("BTCUSDT", "BOTH"): snap}
        )

    def test_position_age_seconds_recent(self) -> None:
        now = datetime.now(UTC)
        pos = _spos("a", entry_time=now - timedelta(seconds=30))
        assert 25 <= _position_age_seconds(pos, now) <= 35

    def test_position_age_seconds_missing_entry_time(self) -> None:
        now = datetime.now(UTC)
        pos = _spos("a", entry_time=None)
        pos["entry_time"] = None
        assert _position_age_seconds(pos, now) == float("inf")

    def test_position_age_seconds_iso_string(self) -> None:
        now = datetime.now(UTC)
        pos = _spos("a")
        pos["entry_time"] = (now - timedelta(seconds=120)).isoformat()
        assert 115 <= _position_age_seconds(pos, now) <= 125


# ---------------------------------------------------------------------------
# reconcile_once — AC1 / AC4
# ---------------------------------------------------------------------------


class TestReconcileOnce:
    @pytest.mark.asyncio
    async def test_no_ghosts_when_exchange_matches(self) -> None:
        live = PositionSnapshot(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        store = await _build_store({("BTCUSDT", "LONG"): live})
        mgr = _manager_with(_spos("a", "BTCUSDT", "LONG"))
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        result = await recon.reconcile_once()

        assert result == {"open": 1, "ghosts": 0, "evicted": 0, "skipped_young": 0}
        assert "a" in mgr.strategy_positions

    @pytest.mark.asyncio
    async def test_ghost_is_evicted_when_old_enough(self) -> None:
        # AC4 — seed StrategyPositionManager with a ghost row, run
        # reconciliation, assert eviction within one cycle.
        store = await _build_store({})  # exchange has nothing
        old_pos = _spos(
            "ghost-1",
            "DOTUSDT",
            "SHORT",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        mgr = _manager_with(old_pos)
        # Use AsyncMock for the Data Manager journal to keep the eviction
        # isolated from real persistence.
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=300)

        result = await recon.reconcile_once()

        assert result["ghosts"] == 1
        assert result["evicted"] == 1
        assert result["skipped_young"] == 0
        assert "ghost-1" not in mgr.strategy_positions
        # Journal must have been called with the closed-externally status.
        assert mgr._update_strategy_position_closure.await_count == 1
        journaled_pos = mgr._update_strategy_position_closure.await_args.args[1]
        assert journaled_pos["status"] == "closed_externally"
        assert journaled_pos["close_reason"] == "no_exchange_position"

    @pytest.mark.asyncio
    async def test_young_ghost_is_skipped(self) -> None:
        # A ghost row younger than min_age_seconds is observed but not
        # evicted — protects against eviction races during placement.
        store = await _build_store({})
        young_pos = _spos(
            "ghost-young",
            "ETHUSDT",
            "LONG",
            entry_time=datetime.now(UTC) - timedelta(seconds=10),
        )
        mgr = _manager_with(young_pos)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=300)

        result = await recon.reconcile_once()

        assert result == {
            "open": 1,
            "ghosts": 1,
            "evicted": 0,
            "skipped_young": 1,
        }
        assert "ghost-young" in mgr.strategy_positions
        mgr._update_strategy_position_closure.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_real_and_ghost(self) -> None:
        live = PositionSnapshot(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        store = await _build_store({("BTCUSDT", "LONG"): live})
        real_pos = _spos(
            "real-1",
            "BTCUSDT",
            "LONG",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        ghost_pos = _spos(
            "ghost-1",
            "LINKUSDT",
            "SHORT",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        mgr = _manager_with(real_pos, ghost_pos)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        result = await recon.reconcile_once()

        assert result["ghosts"] == 1
        assert result["evicted"] == 1
        assert "real-1" in mgr.strategy_positions
        assert "ghost-1" not in mgr.strategy_positions

    @pytest.mark.asyncio
    async def test_skipped_when_store_not_ready(self) -> None:
        # Per #459 contract: when the store is cold, the reconciler is a
        # no-op (the streaming consumer is still warming up; a pass would
        # falsely flag every position as a ghost).
        store = await _build_store({}, is_ready=False)
        mgr = _manager_with(_spos("a"))
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        result = await recon.reconcile_once()

        assert result == {"open": 0, "ghosts": 0, "evicted": 0, "skipped_young": 0}
        assert "a" in mgr.strategy_positions

    @pytest.mark.asyncio
    async def test_status_other_than_open_is_ignored(self) -> None:
        store = await _build_store({})
        closed_pos = _spos(
            "closed-1",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
            status="closed",
        )
        mgr = _manager_with(closed_pos)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        result = await recon.reconcile_once()

        assert result == {"open": 0, "ghosts": 0, "evicted": 0, "skipped_young": 0}
        assert "closed-1" in mgr.strategy_positions

    @pytest.mark.asyncio
    async def test_one_way_mode_does_not_evict_real_position(self) -> None:
        # Hedge / one-way ambiguity has bitten us before; make sure a
        # one-way LONG on Binance is not mistaken for a ghost when the
        # strategy row is also LONG.
        live = PositionSnapshot(
            symbol="BNBUSDT",
            side="BOTH",
            quantity=2.0,
            entry_price=600,
            unrealized_pnl=0,
        )
        store = await _build_store({("BNBUSDT", "BOTH"): live})
        pos = _spos(
            "bnb-long",
            "BNBUSDT",
            "LONG",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        mgr = _manager_with(pos)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        result = await recon.reconcile_once()

        assert result["evicted"] == 0
        assert "bnb-long" in mgr.strategy_positions


# ---------------------------------------------------------------------------
# start/stop lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_runs_initial_pass(self) -> None:
        # AC1 — start() must run an immediate reconcile before the
        # periodic loop, so a pod that restarted with ghosts cleans up
        # before accepting new work.
        store = await _build_store({})
        ghost = _spos(
            "ghost-init",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        mgr = _manager_with(ghost)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(
            mgr, store, interval_seconds=10_000, min_age_seconds=0
        )

        await recon.start()
        try:
            # Eviction must have happened during start(), not by the loop.
            assert "ghost-init" not in mgr.strategy_positions
        finally:
            await recon.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        store = await _build_store({})
        mgr = _manager_with()
        recon = StrategyPositionReconciler(mgr, store)
        await recon.stop()  # never started
        await recon.start()
        await recon.stop()
        await recon.stop()  # second stop is a no-op

    @pytest.mark.asyncio
    async def test_loop_calls_reconcile_repeatedly(self) -> None:
        store = await _build_store({})
        mgr = _manager_with()
        recon = StrategyPositionReconciler(
            mgr, store, interval_seconds=1, min_age_seconds=0
        )
        # Wrap reconcile_once so we can count invocations.
        original = recon.reconcile_once
        counter = {"n": 0}

        async def _wrapped() -> dict[str, int]:
            counter["n"] += 1
            return await original()

        recon.reconcile_once = _wrapped  # type: ignore[assignment]
        await recon.start()
        # Sleep just long enough for the loop to fire at least once
        # (interval_seconds=1).
        await asyncio.sleep(1.2)
        await recon.stop()
        # start() runs one pass synchronously; the loop should add
        # at least one more on top.
        assert counter["n"] >= 2


# ---------------------------------------------------------------------------
# AC5 — post-reconcile invariant
# ---------------------------------------------------------------------------


class TestPostReconcileInvariant:
    @pytest.mark.asyncio
    async def test_no_remaining_strategy_pos_lacks_exchange_match(self) -> None:
        # AC5 — after a clean pass with no young positions, every open
        # strategy position must have a matching exchange position.  This
        # is the property /positions/stops-health relies on to align with
        # /positions.
        live = PositionSnapshot(
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.5,
            entry_price=50_000,
            unrealized_pnl=0,
        )
        store = await _build_store({("BTCUSDT", "LONG"): live})
        real_pos = _spos(
            "real-1",
            "BTCUSDT",
            "LONG",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        ghost_a = _spos(
            "g1",
            "DOTUSDT",
            "SHORT",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        ghost_b = _spos(
            "g2",
            "ETHUSDT",
            "SHORT",
            entry_time=datetime.now(UTC) - timedelta(hours=2),
        )
        mgr = _manager_with(real_pos, ghost_a, ghost_b)
        mgr._update_strategy_position_closure = AsyncMock(return_value=None)
        recon = StrategyPositionReconciler(mgr, store, min_age_seconds=0)

        await recon.reconcile_once()

        exchange_positions = store.get_positions()
        remaining = mgr.get_all_open_strategy_positions()
        for pos in remaining:
            assert _has_matching_exchange_position(pos, exchange_positions), (
                f"AC5 violation: {pos['strategy_position_id']} has no exchange match"
            )
