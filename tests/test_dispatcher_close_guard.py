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
