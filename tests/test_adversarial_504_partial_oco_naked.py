"""Adversarial tests for partial OCO failure protection.

The SL is the protection boundary: a posted SL is retained when the TP fails,
while a posted TP remains subject to the existing orphan cleanup path.
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


class TestStopProtectionStillHolds:
    """A posted stop remains active when the counterparty TP fails."""

    @pytest.mark.asyncio
    async def test_surviving_stop_is_retained(self, logger: logging.Logger) -> None:
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
        assert result["protected_sl_only"] is True
        assert result["position_naked"] is False
        assert exch.client._request_futures_api.call_count == 0


class TestProtectedPartialFailureSignal:
    """A partial TP failure reports protected SL-only state to its caller."""

    @pytest.mark.asyncio
    async def test_partial_failure_result_does_not_flag_naked(
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
        assert result.get("protected_sl_only") is True
        assert result.get("position_naked") is False
        assert result.get("requires_remediation") is False


class TestAllStopSurvivorShapes:
    """SL-posted/TP-failed shapes retain protection for either position side."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "symbol,side,sl_ok,tp_ok",
        [
            ("BCHUSDT", "SHORT", True, False),
            ("XLMUSDT", "LONG", True, False),
            ("BCHUSDT", "SHORT", True, False),
        ],
    )
    async def test_every_partial_shape_retains_stop(
        self,
        logger: logging.Logger,
        symbol: str,
        side: str,
        sl_ok: bool,
        tp_ok: bool,
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

        assert exch.client._request_futures_api.call_count == 0
        assert result["status"] == "failed"
        assert result.get("protected_sl_only") is True
        assert result.get("position_naked") is False
        assert result.get("requires_remediation") is False
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
