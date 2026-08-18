"""Unit tests for WS-driven OCO completion nudge (#534, H6 of #977).

The 2-second poll in ``OCOManager._monitor_orders`` owns every OCO
cancel/close decision. #534 lets a FILLED ``ORDER_TRADE_UPDATE`` on an SL/TP
leg observed on the user-data stream WAKE that poll early (bounded by the same
2s ceiling as a backstop) instead of waiting the full interval. The poll stays
authoritative, so the WS path only shortens latency and can never double-cancel.

These tests exercise the wake mechanism directly:

    AC1: A WS fill on a tracked OCO leg sets the wake event (early re-poll).
    AC2: The 2s poll remains the backstop; the flag defaults off (no behaviour
         change when disabled — the event is never consulted).
    AC4: Idempotency — a fill for an unrelated order does not wake the monitor,
         and repeated sets are a harmless no-op (poll still decides).

Related:
    - Issue: https://github.com/PetroSa2/petrosa-tradeengine/issues/534
    - Parent: https://github.com/PetroSa2/petrosa_k8s/issues/977
"""

import asyncio
import logging
from unittest.mock import MagicMock, Mock, patch

import pytest

from tradeengine.dispatcher import Dispatcher, OCOManager


def _make_manager() -> OCOManager:
    return OCOManager(exchange=Mock(), logger=logging.getLogger("test-534"))


def _track_pair(
    mgr: OCOManager,
    *,
    key: str = "BTCUSDT_LONG",
    symbol: str = "BTCUSDT",
    sl: str = "111",
    tp: str = "222",
) -> None:
    mgr.active_oco_pairs[key] = [
        {
            "symbol": symbol,
            "position_side": "LONG",
            "status": "active",
            "sl_order_id": sl,
            "tp_order_id": tp,
        }
    ]


def test_ac1_ws_fill_on_tracked_sl_leg_sets_wake_event() -> None:
    """A FILLED WS event on a tracked SL leg wakes the monitor."""
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")
    assert not mgr._oco_wake_event.is_set()

    mgr.notify_oco_leg_fill("BTCUSDT", "111")

    assert mgr._oco_wake_event.is_set()


def test_ac1_ws_fill_on_tracked_tp_leg_sets_wake_event() -> None:
    """A FILLED WS event on a tracked TP leg wakes the monitor."""
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")

    mgr.notify_oco_leg_fill("BTCUSDT", "222")

    assert mgr._oco_wake_event.is_set()


def test_ac4_unrelated_order_does_not_wake() -> None:
    """A fill for an order that is not part of any tracked pair is ignored."""
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")

    mgr.notify_oco_leg_fill("BTCUSDT", "999")  # unknown order id
    assert not mgr._oco_wake_event.is_set()

    mgr.notify_oco_leg_fill("ETHUSDT", "111")  # right id, wrong symbol
    assert not mgr._oco_wake_event.is_set()


def test_ac4_idempotent_repeated_sets_are_noop() -> None:
    """Calling notify repeatedly is safe — the event stays set, poll decides."""
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")

    mgr.notify_oco_leg_fill("BTCUSDT", "111")
    mgr.notify_oco_leg_fill("BTCUSDT", "111")
    mgr.notify_oco_leg_fill("BTCUSDT", "222")

    assert mgr._oco_wake_event.is_set()


def test_no_tracked_pairs_is_safe() -> None:
    """No active pairs -> notify is a harmless no-op."""
    mgr = _make_manager()
    mgr.notify_oco_leg_fill("BTCUSDT", "111")
    assert not mgr._oco_wake_event.is_set()


@pytest.mark.asyncio
async def test_ac1_wake_event_interrupts_wait_early() -> None:
    """When set, the wake event releases an awaiter well under the 2s ceiling.

    Mirrors the interruptible-sleep the monitor performs when the flag is on:
    ``await asyncio.wait_for(self._oco_wake_event.wait(), timeout=2)``.
    """
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")

    async def waiter() -> float:
        loop = asyncio.get_event_loop()
        start = loop.time()
        try:
            await asyncio.wait_for(mgr._oco_wake_event.wait(), timeout=2)
        except TimeoutError:
            pass
        return loop.time() - start

    task = asyncio.ensure_future(waiter())
    await asyncio.sleep(0.05)
    mgr.notify_oco_leg_fill("BTCUSDT", "111")  # WS fill arrives
    elapsed = await task

    assert elapsed < 1.0  # woke early, nowhere near the 2s backstop ceiling


@pytest.mark.asyncio
async def test_ac2_backstop_timeout_when_no_wake() -> None:
    """With no fill signal, the awaiter falls through on the 2s backstop."""
    mgr = _make_manager()
    _track_pair(mgr, sl="111", tp="222")

    timed_out = False
    try:
        await asyncio.wait_for(mgr._oco_wake_event.wait(), timeout=0.1)
    except TimeoutError:
        timed_out = True

    assert timed_out  # poll still fires on its interval — backstop intact


# ---------------------------------------------------------------------------
# Dispatcher._on_user_data_fill — callback → wake integration (flag-gated).
# ---------------------------------------------------------------------------


def _bare_dispatcher_with_pair() -> Dispatcher:
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.oco_manager = _make_manager()
    _track_pair(d.oco_manager, sl="111", tp="222")
    return d


@pytest.mark.asyncio
async def test_ac1_callback_wakes_monitor_when_flag_on() -> None:
    """A FILLED reduce-only SL leg wakes the monitor when the flag is on."""
    d = _bare_dispatcher_with_pair()
    order_obj = {
        "s": "BTCUSDT",
        "i": 111,  # matches the tracked SL leg
        "X": "FILLED",
        "S": "SELL",
        "o": "STOP_MARKET",
        "R": True,
    }
    with patch("tradeengine.dispatcher.settings") as st:
        st.te_oco_ws_wake_enabled = True
        await d._on_user_data_fill(order_obj)

    assert d.oco_manager._oco_wake_event.is_set()


@pytest.mark.asyncio
async def test_ac2_callback_does_not_wake_when_flag_off() -> None:
    """With the flag off, the callback never touches the wake event."""
    d = _bare_dispatcher_with_pair()
    order_obj = {
        "s": "BTCUSDT",
        "i": 111,
        "X": "FILLED",
        "S": "SELL",
        "o": "STOP_MARKET",
        "R": True,
    }
    with patch("tradeengine.dispatcher.settings") as st:
        st.te_oco_ws_wake_enabled = False
        await d._on_user_data_fill(order_obj)

    assert not d.oco_manager._oco_wake_event.is_set()
