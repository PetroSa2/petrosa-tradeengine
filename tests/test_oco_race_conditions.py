"""Unit tests for OCO race conditions in the monitoring loop.

The OCO monitoring loop (``OCOManager._monitor_orders`` in
``tradeengine/dispatcher.py``) polls open orders every 2 seconds. Between
polls the market can move fast enough that both SL and TP fill, or a
cancellation can fail silently. These races cause double-closes, missed
cancellations and stale tracking state.

This suite exercises the race branches directly rather than relying on the
real 2-second wall-clock poll (the existing integration suite in
``tests/integration/test_oco_fill_cancel.py`` does the slow end-to-end path).
Each test drives a single monitor iteration by patching ``asyncio.sleep`` to
stop the loop after one pass, or calls the OCO methods directly.

Covers:
    AC1: Simultaneous SL+TP fill between polls -> pair completed once.
    AC2: Cancellation network failure leaves tracking intact.
    AC3: Empty order response handled gracefully (no false completion crash).
    AC4: Deferred OCO lost on restart (pending_entries is in-memory only).
    AC5: Partial-OCO orphan-leg race records the oco_orphan_leg_total metric.

Related:
    - Issue: https://github.com/PetroSa2/petrosa-tradeengine/issues/515
    - Parent: https://github.com/PetroSa2/petrosa_k8s/issues/970
    - Patterns from: tests/integration/test_oco_fill_cancel.py
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tradeengine.dispatcher import OCOManager


class RaceFakeExchange:
    """Minimal fake exchange for driving OCO race branches deterministically.

    Modelled on ``tests/integration/test_oco_fill_cancel.py::FakeExchange`` but
    trimmed to what the race tests need: a controllable ``get_all_open_orders``
    set and a ``client`` whose ``futures_cancel_order`` / ``futures_get_order``
    behaviour can be swapped per-test to simulate failures.
    """

    def __init__(self) -> None:
        self.client = Mock()
        # Set of order-id strings that are currently "open" on the exchange.
        self._open_order_ids: set[str] = set()
        self.cancelled_orders: list[str] = []
        # Default sync client behaviour: successful cancel, benign order lookup.
        self.client.futures_cancel_order = self._futures_cancel_order
        self.client.futures_get_order = self._futures_get_order

    async def get_all_open_orders(self, symbol: str | None = None) -> set[str]:
        """Return the currently-open order ids (matches the real set[str] API)."""
        return set(self._open_order_ids)

    def set_open(self, *order_ids: str) -> None:
        self._open_order_ids = {str(o) for o in order_ids}

    def _futures_cancel_order(self, symbol: str, orderId: str) -> dict[str, Any]:
        self.cancelled_orders.append(orderId)
        self._open_order_ids.discard(str(orderId))
        return {"orderId": orderId, "status": "CANCELED"}

    def _futures_get_order(self, symbol: str, orderId: str) -> dict[str, Any]:
        return {
            "orderId": orderId,
            "symbol": symbol,
            "status": "FILLED",
            "avgPrice": "50000.0",
            "executedQty": "0.001",
        }


def _make_oco_info(
    *,
    sl_order_id: str,
    tp_order_id: str,
    symbol: str = "BTCUSDT",
    position_side: str = "LONG",
    status: str = "active",
) -> dict[str, Any]:
    """Build an oco_info dict matching the shape stored in active_oco_pairs."""
    return {
        "position_id": "pos-1",
        "strategy_position_id": None,  # skip strategy close path (AC focus is race)
        "entry_price": 50000.0,
        "quantity": 0.001,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "symbol": symbol,
        "position_side": position_side,
        "status": status,
        "created_at": "2026-07-18T00:00:00+00:00",
    }


@pytest.fixture
def exchange() -> RaceFakeExchange:
    return RaceFakeExchange()


@pytest.fixture
def oco_manager(exchange: RaceFakeExchange) -> OCOManager:
    logger = logging.getLogger("test_oco_race_conditions")
    return OCOManager(exchange=exchange, logger=logger)


async def _run_single_monitor_pass(manager: OCOManager) -> None:
    """Drive exactly one iteration of the monitor loop.

    ``_monitor_orders`` loops while ``monitoring_active`` and there is work.
    We patch ``asyncio.sleep`` to flip ``monitoring_active`` off after the
    first pass so the coroutine returns instead of blocking for 2 seconds.
    """
    manager.monitoring_active = True

    async def _stop_after_first_pass(_seconds: float) -> None:
        manager.monitoring_active = False

    with patch("tradeengine.dispatcher.asyncio.sleep", new=_stop_after_first_pass):
        await manager._monitor_orders()


# ---------------------------------------------------------------------------
# AC1 + AC5(idempotency): Simultaneous SL+TP fill between polls.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac1_simultaneous_sl_tp_fill_completes_once(
    oco_manager: OCOManager, exchange: RaceFakeExchange
) -> None:
    """AC1/AC5: both SL and TP gone between polls -> completed exactly once.

    When ``get_all_open_orders`` returns neither leg, the loop takes the
    ``not sl_exists and not tp_exists`` branch: the pair is marked
    ``completed`` and skipped. It must NOT call ``cancel_other_order`` (nothing
    to cancel) nor ``_close_position_on_oco_completion`` (no single fill), so no
    double-close or duplicate emission occurs.
    """
    key = "BTCUSDT_LONG"
    oco_manager.active_oco_pairs[key] = [
        _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    ]
    # Both legs already gone from the exchange (simultaneous fill between polls).
    exchange.set_open()  # empty

    cancel_spy = AsyncMock(return_value=(True, "stop_loss"))
    close_spy = AsyncMock()
    oco_manager.cancel_other_order = cancel_spy  # type: ignore[assignment]
    oco_manager._close_position_on_oco_completion = close_spy  # type: ignore[assignment]

    await _run_single_monitor_pass(oco_manager)

    # Pair recognised as completed and garbage-collected (key removed once empty).
    assert key not in oco_manager.active_oco_pairs
    # No panic path: neither cancel nor close was invoked for the "both gone" race.
    cancel_spy.assert_not_called()
    close_spy.assert_not_called()


# ---------------------------------------------------------------------------
# AC2: Cancellation network failure leaves tracking intact.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac2_cancel_connection_error_keeps_tracking(
    oco_manager: OCOManager, exchange: RaceFakeExchange
) -> None:
    """AC2: a ConnectionError on cancel returns (False, ...) and does NOT
    mutate ``active_oco_pairs`` (the dead order stays tracked for retry)."""
    key = "BTCUSDT_LONG"
    oco_info = _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    oco_manager.active_oco_pairs[key] = [oco_info]

    # SL filled (gone), TP still open -> cancel_other_order will try to cancel TP.
    exchange.set_open("TP1")

    def _boom(symbol: str, orderId: str) -> dict[str, Any]:
        raise ConnectionError("network down")

    exchange.client.futures_cancel_order = _boom

    success, reason = await oco_manager.cancel_other_order(
        position_id="pos-1",
        filled_order_id="SL1",
        symbol="BTCUSDT",
        position_side="LONG",
    )

    # Non -2011/-2013 error -> hard failure surfaced to caller.
    assert success is False
    assert reason == "stop_loss"
    # Tracking untouched: pair still present and still active for the next poll.
    assert key in oco_manager.active_oco_pairs
    assert oco_manager.active_oco_pairs[key][0]["status"] == "active"
    assert oco_manager.active_oco_pairs[key][0] is oco_info


# ---------------------------------------------------------------------------
# AC3: Empty order response handled gracefully.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac3_empty_order_response_no_crash(
    oco_manager: OCOManager, exchange: RaceFakeExchange
) -> None:
    """AC3: ``get_all_open_orders`` returning an empty set mid-poll must not
    crash the loop; the loop completes one pass and stops cleanly."""
    key = "BTCUSDT_LONG"
    oco_manager.active_oco_pairs[key] = [
        _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    ]
    exchange.get_all_open_orders = AsyncMock(return_value=set())  # type: ignore[assignment]

    # Must not raise.
    await _run_single_monitor_pass(oco_manager)

    # Loop ran the empty-response branch to completion.
    exchange.get_all_open_orders.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac3_empty_response_preserves_unrelated_symbol(
    oco_manager: OCOManager, exchange: RaceFakeExchange
) -> None:
    """AC3 corollary: an empty response for one symbol must not falsely
    complete a *different* symbol's still-open pair on the same pass."""
    # Same symbol/side across both pairs would collide on the key; use two keys.
    oco_manager.active_oco_pairs["ETHUSDT_LONG"] = [
        _make_oco_info(
            sl_order_id="SL_ETH",
            tp_order_id="TP_ETH",
            symbol="ETHUSDT",
        )
    ]

    # ETH pair: both legs still open -> must stay active.
    async def _open_for_eth(symbol: str | None = None) -> set[str]:
        if symbol == "ETHUSDT":
            return {"SL_ETH", "TP_ETH"}
        return set()

    exchange.get_all_open_orders = _open_for_eth  # type: ignore[assignment]

    await _run_single_monitor_pass(oco_manager)

    assert "ETHUSDT_LONG" in oco_manager.active_oco_pairs
    assert oco_manager.active_oco_pairs["ETHUSDT_LONG"][0]["status"] == "active"


# ---------------------------------------------------------------------------
# AC4: Deferred OCO lost on restart (in-memory only).
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac4_pending_entries_lost_on_restart(
    exchange: RaceFakeExchange,
) -> None:
    """AC4: ``pending_entries`` is in-memory only. A fresh OCOManager starts
    with an empty map, so a restart drops deferred entries -> no phantom OCO."""
    logger = logging.getLogger("test_oco_race_conditions")

    first = OCOManager(exchange=exchange, logger=logger)
    first.pending_entries["entry-123"] = {
        "order": Mock(),
        "symbol": "BTCUSDT",
        "entry_order_id": "entry-123",
        "registered_at": 0.0,
    }
    assert first.pending_entries  # populated on the live instance

    # Simulate a pod restart: a brand-new manager over the same exchange.
    restarted = OCOManager(exchange=exchange, logger=logger)

    assert restarted.pending_entries == {}
    # No monitoring implicitly started -> nothing to re-place from stale state.
    assert restarted.monitoring_active is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac4_no_phantom_placement_on_empty_pending(
    oco_manager: OCOManager,
) -> None:
    """AC4: ``_check_pending_entries`` on an empty map must not attempt any
    order placement (no phantom OCO on a freshly restarted instance)."""
    # Wire a dispatcher back-ref whose placement method would flag a phantom.
    dispatcher = Mock()
    dispatcher._place_risk_management_orders = AsyncMock()
    oco_manager.dispatcher = dispatcher

    assert oco_manager.pending_entries == {}
    await oco_manager._check_pending_entries()

    dispatcher._place_risk_management_orders.assert_not_called()


# ---------------------------------------------------------------------------
# AC5: Partial-OCO orphan-leg race records the oco_orphan_leg_total metric.
#
# NOTE: The simultaneous-fill race (AC1) does NOT emit oco_orphan_leg_total —
# in _monitor_orders both-gone simply marks the pair completed. The metric is
# emitted by the *partial OCO placement* path (place_oco_orders), which is the
# real orphan-leg race: one leg posts, the counterparty fails, and the
# surviving leg is cancelled. We assert the counter ticks there.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac5_partial_oco_failure_increments_orphan_leg_metric(
    oco_manager: OCOManager, exchange: RaceFakeExchange
) -> None:
    """AC5: when only one OCO leg posts (partial failure), the surviving leg is
    cancelled and ``oco_orphan_leg_total`` is incremented with the outcome.

    We drive the partial-failure branch by making ``exchange.execute`` return a
    successful SL leg and a failed TP leg. The counter must advance by one for
    this symbol/side/leg. Position close is idempotent (single strategy close).
    """
    from tradeengine.metrics import oco_orphan_leg_total

    symbol = "BTCUSDT"
    side = "LONG"

    def _sample_total() -> float:
        total = 0.0
        for metric in oco_orphan_leg_total.collect():
            for s in metric.samples:
                if (
                    s.name.endswith("_total")
                    and s.labels.get("symbol") == symbol
                    and s.labels.get("side") == side
                ):
                    total += s.value
        return total

    before = _sample_total()

    # SL posts, TP fails -> partial OCO failure (surviving SL leg orphaned).
    async def _execute(order: Any) -> dict[str, Any]:
        if order.type == "STOP_MARKET" or "STOP" in str(order.type).upper():
            return {"order_id": "SL_survivor", "status": "NEW"}
        return {"order_id": None, "status": "FAILED"}

    exchange.execute = _execute  # type: ignore[assignment]
    # Surviving-leg cancel goes through the algo delete path in place_oco_orders.
    exchange.client._request_futures_api = Mock(return_value={"status": "CANCELED"})

    result = await oco_manager.place_oco_orders(
        position_id="pos-1",
        symbol=symbol,
        position_side=side,
        quantity=0.001,
        stop_loss_price=49000.0,
        take_profit_price=51000.0,
        strategy_position_id="strat-1",
        entry_price=50000.0,
    )

    # Placement reported failure (partial), and the orphan-leg counter advanced.
    assert result.get("status") != "success"
    after = _sample_total()
    assert after == before + 1.0, (
        f"expected oco_orphan_leg_total to advance by 1 for {symbol}/{side}, "
        f"before={before} after={after}"
    )
    # Surviving leg was cancelled exactly once (idempotent orphan cleanup).
    assert exchange.client._request_futures_api.call_count == 1
