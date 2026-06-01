"""Tests for `OCOManager` partial-failure handling.

Covers AC1 of petrosa-tradeengine#425 (RC#1 of #424): when one leg posts
successfully and the other fails, `place_oco_orders` MUST cancel the
surviving leg on Binance before returning ``{"status": "failed"}``.

Equivalent to ``test_h1_surviving_sl_leg_is_cancelled_when_tp_leg_fails``
in ``petrosa_k8s/_bmad-output/incidents/2026-05-30/reproduction_test.py``.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test-oco-manager-425")


def _make_exchange(sl_ok: bool, tp_ok: bool) -> AsyncMock:
    """Build an exchange double whose `execute` returns success/failure per leg.

    The execute mock returns an order_id only when that leg is configured to
    succeed; otherwise it returns ``order_id: None``. `client._request_futures_api`
    is a `MagicMock` so call kwargs can be asserted.
    """
    exch = AsyncMock()
    exch.client = MagicMock()

    sl_id = "1000000091274545"
    tp_id = "1000000091274546"

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


@pytest.mark.asyncio
async def test_surviving_sl_leg_is_cancelled_when_tp_leg_fails(
    logger: logging.Logger,
) -> None:
    """SL posts → TP fails → SL algoId must be sent to algoOrder DELETE."""
    exch = _make_exchange(sl_ok=True, tp_ok=False)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-sl-orphan",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )

    assert result["status"] == "failed"
    assert exch.client._request_futures_api.call_count == 1
    call = exch.client._request_futures_api.call_args
    assert call.args[0] == "delete"
    assert call.args[1] == "algoOrder"
    assert call.kwargs["data"] == {"symbol": "BCHUSDT", "algoId": "1000000091274545"}


@pytest.mark.asyncio
async def test_surviving_tp_leg_is_cancelled_when_sl_leg_fails(
    logger: logging.Logger,
) -> None:
    """TP posts → SL fails → TP algoId must be sent to algoOrder DELETE."""
    exch = _make_exchange(sl_ok=False, tp_ok=True)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-tp-orphan",
        symbol="BCHUSDT",
        position_side="SHORT",
        quantity=0.22,
        stop_loss_price=310.0,
        take_profit_price=300.0,
    )

    assert result["status"] == "failed"
    assert exch.client._request_futures_api.call_count == 1
    call = exch.client._request_futures_api.call_args
    assert call.kwargs["data"] == {"symbol": "BCHUSDT", "algoId": "1000000091274546"}


@pytest.mark.asyncio
async def test_both_legs_fail_does_not_call_cancel(
    logger: logging.Logger,
) -> None:
    """No surviving leg → no cancel attempt."""
    exch = _make_exchange(sl_ok=False, tp_ok=False)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-both-fail",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )

    assert result["status"] == "failed"
    assert exch.client._request_futures_api.call_count == 0


@pytest.mark.asyncio
async def test_orphan_counter_increments_when_cancel_raises(
    logger: logging.Logger,
) -> None:
    """When the cancel itself raises, ``oco_orphan_leg_total`` MUST tick."""
    from tradeengine.metrics import oco_orphan_leg_total

    exch = _make_exchange(sl_ok=True, tp_ok=False)
    exch.client._request_futures_api.side_effect = RuntimeError("binance down")
    oco = OCOManager(exchange=exch, logger=logger)

    sample = oco_orphan_leg_total.labels(symbol="BCHUSDT", side="LONG", leg="SL")
    before = sample._value.get()
    result = await oco.place_oco_orders(
        position_id="ac1-cancel-failed",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )
    after = sample._value.get()

    assert result["status"] == "failed"
    assert after - before == 1.0
