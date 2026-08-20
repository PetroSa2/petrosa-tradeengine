"""Regression for #549: a stale/wrong LOCAL snapshot must never cause a
wrong-side or phantom arm.

Decision (docs/POSITION_STATE_TRUTH_DECISION.md): Option A — corrected mirror.
The contract that makes -4509 (arming a position Binance no longer holds) and
-4130 (arming a wrong-side/duplicate leg) impossible under normal operation is:

    SL/TP arming keys qty/side off EXCHANGE TRUTH, not the local mirror.

These tests exercise the live-arming gate (`OCOManager.place_oco_orders` +
`_fetch_binance_position_qty`, reading Binance `positionRisk`) with a local
snapshot that is deliberately stale or malformed, and assert no phantom /
wrong-side order reaches the exchange.

The `positionAmt = -0.303` LONG shape from the live 2026-08-20 evidence is the
malformed-sign case: a LONG leg holding a negative amount.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture(autouse=True)
def enable_ac3_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """#549 Option A requires the live arming gate ON so arming keys off
    exchange truth. Ship-off default is flipped for these tests."""
    monkeypatch.setenv("TE_OCO_AC3_GATE_ENABLED", "1")


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test.549.stale-snapshot")


@pytest.fixture
def exchange_mock() -> AsyncMock:
    """Exchange that would succeed if placement ever reached it."""
    exchange = AsyncMock()
    exchange.execute = AsyncMock(
        return_value={"order_id": "1234567890123", "status": "FILLED"}
    )
    return exchange


def _dispatcher_reading_live(position_risk: list[dict[str, Any]]) -> Any:
    """Stub dispatcher whose _fetch_binance_position_qty derives qty/side from
    the SAME sign-normalization logic production uses (matches
    dispatcher._fetch_binance_position_qty at dispatcher.py:4876-4924).

    The point of #549: this reads EXCHANGE truth, never the local mirror.
    """

    class _StubDispatcher:
        async def _fetch_binance_position_qty(
            self, symbol: str, position_side: str | None
        ) -> float:
            target_side = (position_side or "").upper()
            for pos in position_risk:
                if pos.get("symbol") != symbol:
                    continue
                side = str(pos.get("positionSide", "BOTH")).upper()
                try:
                    raw_amt = float(pos.get("positionAmt", 0))
                except (TypeError, ValueError):
                    continue
                if side == "BOTH":
                    if raw_amt == 0:
                        continue
                    effective_side = "LONG" if raw_amt > 0 else "SHORT"
                    if target_side and effective_side != target_side:
                        continue
                elif side in ("LONG", "SHORT"):
                    if target_side and side != target_side:
                        continue
                qty = abs(raw_amt)
                if qty > 0:
                    return qty
            return 0.0

    return _StubDispatcher()


@pytest.mark.asyncio
async def test_stale_local_position_closed_on_exchange_no_phantom_arm(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Local mirror still says LTCUSDT LONG 0.303 (stale, e.g. after restart),
    but Binance positionRisk is empty → arming must be skipped, no order sent.

    This is the -4509 case: arming a position Binance no longer holds.
    """
    dispatcher = _dispatcher_reading_live(position_risk=[])  # exchange holds nothing
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    # Caller passes the STALE local quantity — the gate must ignore it in
    # favour of exchange truth.
    result = await oco.place_oco_orders(
        position_id="pos-ltc",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.303,  # stale local value
        stop_loss_price=44.0,
        take_profit_price=49.0,
    )

    assert result["status"] == "skipped_no_position_on_exchange"
    assert result["sl_order_id"] is None
    assert result["tp_order_id"] is None
    exchange_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_negative_long_snapshot_resolves_via_exchange_truth(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """The live -0.303 LONG shape: a malformed local snapshot must not drive
    arming. Exchange truth (BOTH row, negative amt) resolves to a SHORT, so a
    LONG arm request finds no matching live LONG → skip (no wrong-side arm).
    """
    # ONE-WAY 'BOTH' row with negative amt = a real SHORT of 0.303, NOT a LONG.
    position_risk = [
        {"symbol": "LTCUSDT", "positionSide": "BOTH", "positionAmt": "-0.303"}
    ]
    dispatcher = _dispatcher_reading_live(position_risk)
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    # Local mirror wrongly labelled this a LONG; request a LONG arm.
    result = await oco.place_oco_orders(
        position_id="pos-ltc",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.303,
        stop_loss_price=44.0,
        take_profit_price=49.0,
    )

    # No live LONG exists → gate skips, no wrong-side leg reaches the exchange.
    assert result["status"] == "skipped_no_position_on_exchange"
    exchange_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_matching_exchange_truth_still_allows_correct_arm(
    logger: logging.Logger, exchange_mock: AsyncMock
) -> None:
    """Control: when the local request DOES match live exchange truth, arming
    proceeds. The gate blocks phantom/wrong-side arms, not legitimate ones.
    """
    position_risk = [
        {"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "0.303"}
    ]
    dispatcher = _dispatcher_reading_live(position_risk)
    oco = OCOManager(exchange=exchange_mock, logger=logger, dispatcher=dispatcher)

    result = await oco.place_oco_orders(
        position_id="pos-ltc",
        symbol="LTCUSDT",
        position_side="LONG",
        quantity=0.303,
        stop_loss_price=44.0,
        take_profit_price=49.0,
    )

    assert result.get("status") != "skipped_no_position_on_exchange"
    assert exchange_mock.execute.await_count == 2
