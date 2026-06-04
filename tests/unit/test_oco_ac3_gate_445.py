"""AC3 of #445: OCOManager.place_oco_orders must skip placement when
Binance reports no open position for (symbol, position_side).

Without this gate, restarting after a Mongo blip / stale local state
fires reduceOnly/closePosition orders against positions Binance no
longer holds → APIError(-4509) "TIF GTE can only be used with open
positions" loop.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test.oco.ac3")


@pytest.fixture
def exchange_mock() -> AsyncMock:
    """A minimal exchange mock that would succeed if placement reached it."""
    exchange = AsyncMock()
    exchange.execute = AsyncMock(
        return_value={"order_id": "1234567890123", "status": "FILLED"}
    )
    return exchange


def _make_dispatcher_with_qty(qty: float) -> Any:
    """Stub dispatcher exposing the _fetch_binance_position_qty contract
    used by the AC3 gate."""

    class _StubDispatcher:
        async def _fetch_binance_position_qty(
            self, symbol: str, position_side: str | None
        ) -> float:
            return qty

    return _StubDispatcher()


def _make_dispatcher_that_raises() -> Any:
    class _StubDispatcher:
        async def _fetch_binance_position_qty(
            self, symbol: str, position_side: str | None
        ) -> float:
            raise RuntimeError("boom")

    return _StubDispatcher()


# ---------------------------------------------------------------------------
# Gate fires — no position on exchange
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_gate_skips_when_position_absent(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Position not on exchange → skip placement, no exchange.execute call."""
    dispatcher = _make_dispatcher_with_qty(0.0)
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    result = await oco.place_oco_orders(
        position_id="pos1",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=50000.0,
        take_profit_price=55000.0,
    )

    assert result["status"] == "skipped_no_position_on_exchange"
    assert result["sl_order_id"] is None
    assert result["tp_order_id"] is None
    # Critical: exchange.execute MUST NOT be called — that's how we kill
    # the -4509 GTE loop.
    exchange_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_ac3_gate_skips_when_position_qty_near_zero(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Sub-tolerance qty (e.g. 1e-12) counts as absent."""
    dispatcher = _make_dispatcher_with_qty(1e-12)
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    result = await oco.place_oco_orders(
        position_id="pos1",
        symbol="BTCUSDT",
        position_side="SHORT",
        quantity=0.01,
        stop_loss_price=55000.0,
        take_profit_price=50000.0,
    )

    assert result["status"] == "skipped_no_position_on_exchange"
    exchange_mock.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Gate falls through — position present or verification unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_gate_falls_through_when_position_exists(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Position present on exchange → gate is a no-op, placement proceeds."""
    dispatcher = _make_dispatcher_with_qty(0.01)
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    result = await oco.place_oco_orders(
        position_id="pos1",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=50000.0,
        take_profit_price=55000.0,
    )

    # Placement must reach the exchange (both SL and TP) — gate did not fire.
    assert exchange_mock.execute.await_count == 2
    # Status is the placement outcome, not the gate skip sentinel.
    assert result.get("status") != "skipped_no_position_on_exchange"


@pytest.mark.asyncio
async def test_ac3_gate_falls_through_when_lookup_raises(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Lookup raised → fall through to placement; never silently drop OCO.

    Rationale: a transient exchange API error must not cause us to skip
    arming an actual open position — that would re-introduce the very
    naked-position class this PR is preventing. The gate is defensive,
    not authoritative.
    """
    dispatcher = _make_dispatcher_that_raises()
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    result = await oco.place_oco_orders(
        position_id="pos1",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=50000.0,
        take_profit_price=55000.0,
    )

    assert exchange_mock.execute.await_count == 2
    assert result.get("status") != "skipped_no_position_on_exchange"


@pytest.mark.asyncio
async def test_ac3_gate_no_dispatcher_falls_through(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """No dispatcher injected → gate cannot run; placement proceeds.

    OCOManager has a long history of being constructed with dispatcher=None
    in test contexts. The AC3 gate must remain optional so we don't
    break those — but production wires the dispatcher in.
    """
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=None)

    result = await oco.place_oco_orders(
        position_id="pos1",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=50000.0,
        take_profit_price=55000.0,
    )

    assert exchange_mock.execute.await_count == 2
    assert result.get("status") != "skipped_no_position_on_exchange"
