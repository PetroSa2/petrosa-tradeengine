"""Adversarial tests for tradeengine#504 — partial OCO failure leaves naked position.

IMPORTANT interaction with #482/#425: those tickets deliberately made OCO
atomic — on a partial fill the surviving leg MUST be cancelled to avoid an
orphan reduce-only order (the -4509 loop). We do NOT want to revert that.

The #504 defect is what happens AFTER the surviving leg is cancelled:
``OCOManager.place_oco_orders`` returns ``{"status": "failed"}`` (dispatcher.py
:466) and the dispatcher falls back to ``_place_individual_risk_orders``, which
retries both legs with the SAME uncorrected prices. When those also fail (the
2026-07-16 -2021 storm), there is NO terminal safety net — the position is left
fully naked and nothing flattens or escalates it.

Correct behavior (the fix): after OCO partial-failure AND fallback exhaustion,
the position must not be silently naked — it must be escalated to remediation
(re-arm with corrected price, or flatten). This test asserts the escalation
contract at the OCO-result level.

Two guards:
  1. Keep #482 atomicity: surviving leg IS cancelled on partial failure (green).
  2. #504: the failed OCO result must carry an escalation/naked signal the
     caller can act on — not an opaque "failed" that gets silently dropped.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test-adversarial-504")


def _make_exchange(sl_ok: bool, tp_ok: bool) -> AsyncMock:
    exch = AsyncMock()
    exch.client = MagicMock()
    sl_id, tp_id = "1000000091274545", "1000000091274546"

    async def execute(order: Any) -> dict[str, Any]:
        if str(order.type) in ("OrderType.STOP", "stop"):
            return {
                "order_id": sl_id if sl_ok else None,
                "status": "NEW" if sl_ok else "failed",
            }
        return {
            "order_id": tp_id if tp_ok else None,
            "status": "NEW" if tp_ok else "failed",
        }

    exch.execute = execute
    return exch


class TestAtomicityStillHolds:
    """#482 must not regress: surviving leg is cancelled on partial failure."""

    @pytest.mark.asyncio
    async def test_surviving_leg_cancelled(self, logger: logging.Logger) -> None:
        exch = _make_exchange(sl_ok=True, tp_ok=False)
        oco = OCOManager(exchange=exch, logger=logger)
        result = await oco.place_oco_orders(
            position_id="p",
            symbol="BCHUSDT",
            position_side="LONG",
            quantity=0.22,
            stop_loss_price=200.0,
            take_profit_price=260.0,
        )
        assert result["status"] == "failed"
        # surviving SL leg cancelled (atomicity preserved)
        assert exch.client._request_futures_api.call_count == 1


class TestNakedEscalationSignal:
    """#504: partial OCO failure must surface an actionable naked/escalation
    signal so the position is never silently left unprotected."""

    @pytest.mark.asyncio
    async def test_partial_failure_result_flags_naked_for_escalation(
        self, logger: logging.Logger
    ) -> None:
        exch = _make_exchange(sl_ok=True, tp_ok=False)
        oco = OCOManager(exchange=exch, logger=logger)
        result = await oco.place_oco_orders(
            position_id="p",
            symbol="BCHUSDT",
            position_side="LONG",
            quantity=0.22,
            stop_loss_price=200.0,
            take_profit_price=260.0,
        )
        assert result["status"] == "failed"
        # Post-fix: the result must let the caller know the position is now
        # unprotected and requires remediation — not just "failed".
        assert (
            result.get("position_naked") is True
            or result.get("requires_remediation") is True
            or result.get("escalate") is True
        ), (
            "Partial OCO failure returns opaque 'failed' with no naked/escalation "
            "signal — caller cannot distinguish 'unprotected position' from a "
            "benign rejection (#504)"
        )


class TestAllPartialFailureShapes504:
    """#504 AC4: regression coverage for all four 2026-07-16 partial-failure
    shapes. Each ends with the position naked (surviving leg cancelled) and
    MUST now carry the naked/escalation signal so remediation can act.

    Evidence shapes from the incident:
      - LINKUSDT LONG  — TP posted, SL failed  → cancel surviving TP → naked
      - BCHUSDT SHORT  — SL posted, TP failed  → cancel surviving SL → naked
      - XLMUSDT LONG   — SL posted, TP failed  → cancel surviving SL → naked
      - BCHUSDT SHORT  — SL posted, TP failed  → cancel surviving SL → naked
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "symbol,side,sl_ok,tp_ok,expected_cancelled_leg",
        [
            # TP posted / SL failed → surviving TP cancelled
            ("LINKUSDT", "LONG", False, True, "TP"),
            # SL posted / TP failed → surviving SL cancelled
            ("BCHUSDT", "SHORT", True, False, "SL"),
            ("XLMUSDT", "LONG", True, False, "SL"),
            ("BCHUSDT", "SHORT", True, False, "SL"),
        ],
    )
    async def test_every_partial_shape_flags_naked_and_cancels_survivor(
        self,
        logger: logging.Logger,
        symbol: str,
        side: str,
        sl_ok: bool,
        tp_ok: bool,
        expected_cancelled_leg: str,
    ) -> None:
        exch = _make_exchange(sl_ok=sl_ok, tp_ok=tp_ok)
        oco = OCOManager(exchange=exch, logger=logger)
        result = await oco.place_oco_orders(
            position_id="p",
            symbol=symbol,
            position_side=side,
            quantity=0.22,
            stop_loss_price=200.0 if side == "LONG" else 260.0,
            take_profit_price=260.0 if side == "LONG" else 200.0,
        )

        # Atomicity preserved (#482): surviving leg cancelled exactly once.
        assert exch.client._request_futures_api.call_count == 1

        # #504: naked/escalation signal present and correct.
        assert result["status"] == "failed"
        assert result.get("position_naked") is True
        assert result.get("requires_remediation") is True
        assert result.get("escalate") is True
        assert result.get("cancelled_leg") == expected_cancelled_leg
        assert result.get("symbol") == symbol
        assert result.get("position_side") == side

    @pytest.mark.asyncio
    async def test_total_leg_failure_is_not_flagged_naked(
        self, logger: logging.Logger
    ) -> None:
        """Both legs fail (nothing posted) → no surviving leg to cancel and no
        naked position was created by us. Must NOT emit the #504 naked signal
        (that would misroute a benign rejection into remediation)."""
        exch = _make_exchange(sl_ok=False, tp_ok=False)
        oco = OCOManager(exchange=exch, logger=logger)
        result = await oco.place_oco_orders(
            position_id="p",
            symbol="ETHUSDT",
            position_side="LONG",
            quantity=0.22,
            stop_loss_price=200.0,
            take_profit_price=260.0,
        )
        assert result["status"] == "failed"
        # No surviving leg → no cancel call, no naked flag.
        assert exch.client._request_futures_api.call_count == 0
        assert result.get("position_naked") is None
        assert result.get("requires_remediation") is None
