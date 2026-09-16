"""Integration tests for the #481 close guard + thrash circuit-breaker.

Covers the emission-time defenses added to
``Dispatcher.close_position_with_cleanup``:

- AC1/AC3: seeding a ghost strategy SHORT for a symbol that has a real LONG on
  the exchange reproduces the thrash trigger; the guard blocks the reduceOnly
  close and increments ``strategy_close_blocked_no_exchange_position_total``.
- AC3: a matching exchange position lets the close proceed; an un-ready
  ExchangeTruthStore does NOT block (avoids suppressing a legitimate close).
- AC5: repeated un-audited closes on the same symbol trip the circuit-breaker
  and increment ``dispatcher_thrash_circuit_open_total``; audited closes flow.
- #586: a requested closing quantity larger than the live Binance position is
  clamped (never overshoots into a sign-inverted "malformed position");
  a confidently-flat live position skips the close outright; an unknown live
  reading (store not ready, REST lookup ambiguous) does not block or clamp.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot


class _FakeConsumer:
    def __init__(self, store: ExchangeTruthStore) -> None:
        self.store = store


def _dispatcher_with_positions(
    positions: dict[tuple[str, str], PositionSnapshot] | None,
    *,
    store_ready: bool = True,
) -> Dispatcher:
    """Build a Dispatcher with a mocked exchange and a seeded truth store."""
    exchange = AsyncMock()
    exchange.execute = AsyncMock(
        return_value={"status": "FILLED", "order_id": "close-1"}
    )
    disp = Dispatcher(exchange=exchange)
    # Isolate OCO cleanup — no active pairs for the position under test.
    disp.oco_manager.active_oco_pairs = {}
    disp.position_manager.close_position_record = AsyncMock()

    store = ExchangeTruthStore()
    if positions:
        store._positions = dict(positions)
    store._is_ready = store_ready
    disp.user_data_consumer = _FakeConsumer(store)  # type: ignore[assignment]
    return disp


@pytest.mark.asyncio
async def test_ac3_blocks_close_when_no_exchange_position() -> None:
    # Real LONG on the exchange; a ghost SHORT drives the spurious close.
    positions = {
        ("BNBUSDT", "LONG"): PositionSnapshot(
            symbol="BNBUSDT",
            side="LONG",
            quantity=0.17,
            entry_price=600.0,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="BNBUSDT",
        position_side="SHORT",
        quantity=0.17,
        reason="ghost_short_close",
    )

    assert result["status"] == "skipped_no_exchange_position"
    assert result["position_closed"] is False
    disp.exchange.execute.assert_not_called()


@pytest.mark.asyncio
async def test_ac3_allows_close_when_exchange_position_present() -> None:
    positions = {
        ("BNBUSDT", "LONG"): PositionSnapshot(
            symbol="BNBUSDT",
            side="LONG",
            quantity=0.17,
            entry_price=600.0,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="BNBUSDT",
        position_side="LONG",
        quantity=0.17,
        reason="take_profit",
    )

    assert result["status"] == "success"
    assert result["position_closed"] is True
    disp.exchange.execute.assert_called_once()


@pytest.mark.asyncio
async def test_ac3_unknown_store_does_not_block() -> None:
    # Store not ready -> presence is unknown -> must NOT suppress the close.
    disp = _dispatcher_with_positions(None, store_ready=False)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="ETHUSDT",
        position_side="LONG",
        quantity=1.0,
        reason="manual",
    )

    assert result["status"] == "success"
    disp.exchange.execute.assert_called_once()


@pytest.mark.asyncio
async def test_ac3_guard_disabled_by_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TE_CLOSE_GUARD_ENABLED", "0")
    positions = {
        ("BNBUSDT", "LONG"): PositionSnapshot(
            symbol="BNBUSDT",
            side="LONG",
            quantity=0.17,
            entry_price=600.0,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    # Even with no matching SHORT, the flag-off path emits the close.
    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="BNBUSDT",
        position_side="SHORT",
        quantity=0.17,
        reason="ghost_short_close",
    )

    assert result["status"] == "success"
    disp.exchange.execute.assert_called_once()


@pytest.mark.asyncio
async def test_ac5_thrash_circuit_opens_on_repeated_closes() -> None:
    # A matching LONG exists so AC3 always allows the close; AC5 must be what
    # ultimately stops the churn. Cap defaults to 2 within 10 minutes.
    positions = {
        ("LINKUSDT", "LONG"): PositionSnapshot(
            symbol="LINKUSDT",
            side="LONG",
            quantity=12.37,
            entry_price=15.0,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    async def _close() -> dict:
        return await disp.close_position_with_cleanup(
            position_id="pos-1",
            symbol="LINKUSDT",
            position_side="LONG",
            quantity=12.37,
            reason="thrash",
        )

    first = await _close()
    second = await _close()
    third = await _close()

    assert first["status"] == "success"
    assert second["status"] == "success"
    # Third un-audited close on the same symbol trips the breaker.
    assert third["status"] == "skipped_thrash_circuit_open"


@pytest.mark.asyncio
async def test_ac5_audited_closes_bypass_circuit() -> None:
    positions = {
        ("LINKUSDT", "LONG"): PositionSnapshot(
            symbol="LINKUSDT",
            side="LONG",
            quantity=12.37,
            entry_price=15.0,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    # Many CIO-audited closes never trip the breaker.
    for _ in range(5):
        result = await disp.close_position_with_cleanup(
            position_id="pos-1",
            symbol="LINKUSDT",
            position_side="LONG",
            quantity=12.37,
            reason="cio_decision",
            cio_audited=True,
        )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# #586 — close-quantity clamp against the live Binance position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_586_oversized_close_is_clamped_to_live_qty() -> None:
    """A stale/duplicate close requesting more than the live position size
    must be clamped to the live |positionAmt| — never overshoot into a
    sign-inverted (malformed) position."""
    positions = {
        ("LTCUSDT", "LONG"): PositionSnapshot(
            symbol="LTCUSDT",
            side="LONG",
            quantity=0.303,
            entry_price=47.99,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    # Caller requests closing MORE than the live position holds (e.g. a
    # racing second close trigger computed from a stale snapshot).
    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.5,
        reason="racing_close_trigger",
    )

    assert result["status"] == "success"
    disp.exchange.execute.assert_called_once()
    placed_order = disp.exchange.execute.call_args[0][0]
    # The order sent to the exchange must be clamped to the live qty, never
    # the oversized requested amount — this is what prevents the sign flip.
    assert placed_order.amount == pytest.approx(0.303)


@pytest.mark.asyncio
async def test_586_confidently_flat_position_skips_close() -> None:
    """A store-confirmed-flat (symbol, side) must refuse the close outright
    rather than let an oversized MARKET order flip the position's sign.

    The pre-existing #481 AC3 presence check (step 1b) already catches this
    exact case for a store-ready lookup — the #586 clamp's own
    ``skipped_flat_position`` branch is defense-in-depth for a live_qty of
    literally 0 that somehow survives the presence check. Either way, no
    close order may ever reach the exchange."""
    # Store is ready but holds no row for (LTCUSDT, LONG) at all — the
    # confident-empty case.
    disp = _dispatcher_with_positions({})

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.303,
        reason="stale_close_after_already_flat",
    )

    assert result["status"] == "skipped_no_exchange_position"
    assert result["position_closed"] is False
    disp.exchange.execute.assert_not_called()


@pytest.mark.asyncio
async def test_586_exact_match_is_not_clamped() -> None:
    """Requesting exactly the live quantity must pass through unchanged."""
    positions = {
        ("LTCUSDT", "LONG"): PositionSnapshot(
            symbol="LTCUSDT",
            side="LONG",
            quantity=0.303,
            entry_price=47.99,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.303,
        reason="tp_triggered",
    )

    assert result["status"] == "success"
    placed_order = disp.exchange.execute.call_args[0][0]
    assert placed_order.amount == pytest.approx(0.303)


@pytest.mark.asyncio
async def test_586_malformed_negative_quantity_snapshot_clamped_by_abs() -> None:
    """A malformed LONG row (negative stored quantity, mirroring the raw
    Binance positionAmt sign inversion #586 describes) must still clamp by
    absolute value rather than skip or pass through a negative live_qty."""
    positions = {
        ("LTCUSDT", "LONG"): PositionSnapshot(
            symbol="LTCUSDT",
            side="LONG",
            quantity=-0.303,  # inverted sign, as stored by the WS consumer
            entry_price=47.99,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=1.0,
        reason="malformed_position",
    )

    assert result["status"] == "success"
    placed_order = disp.exchange.execute.call_args[0][0]
    assert placed_order.amount == pytest.approx(0.303)


@pytest.mark.asyncio
async def test_586_unknown_live_qty_does_not_clamp_or_block() -> None:
    """Store not ready AND REST lookup ambiguous (returns 0) must be treated
    as UNKNOWN — never confidently 'flat' — so a legitimate close is not
    suppressed (mirrors the #481 AC3 "unknown must not block" rule)."""
    disp = _dispatcher_with_positions(None, store_ready=False)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="ETHUSDT",
        position_side="LONG",
        quantity=1.0,
        reason="manual",
    )

    assert result["status"] == "success"
    placed_order = disp.exchange.execute.call_args[0][0]
    # Unclamped: the pre-clamp requested quantity passed through unchanged.
    assert placed_order.amount == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_586_clamp_disabled_by_close_guard_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TE_CLOSE_GUARD_ENABLED=0 disables the #586 clamp along with the #481
    presence check — a single operator override switch for both."""
    monkeypatch.setenv("TE_CLOSE_GUARD_ENABLED", "0")
    positions = {
        ("LTCUSDT", "LONG"): PositionSnapshot(
            symbol="LTCUSDT",
            side="LONG",
            quantity=0.303,
            entry_price=47.99,
            unrealized_pnl=0.0,
        )
    }
    disp = _dispatcher_with_positions(positions)

    result = await disp.close_position_with_cleanup(
        position_id="pos-1",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=5.0,  # grossly oversized
        reason="manual",
    )

    assert result["status"] == "success"
    placed_order = disp.exchange.execute.call_args[0][0]
    # Flag off -> no clamp applied, oversized quantity ships as-is.
    assert placed_order.amount == pytest.approx(5.0)
