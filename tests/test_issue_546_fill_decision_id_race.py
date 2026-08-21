"""
Regression tests for tradeengine#546 — execution.events emits no
filled/partial_fill events in prod (trade-audit dashboard is data-empty).

Root cause: ``Dispatcher._on_user_data_fill`` (fired near-instantly by the
Binance user-data WebSocket stream on ORDER_TRADE_UPDATE FILLED) recovered
strategy_id/decision_id exclusively via
``strategy_position_manager.get_strategy_position_by_entry_order_id()``.
That lookup is only populated once ``create_strategy_position()``'s
I/O-bound persistence pipeline completes — which can take seconds, or time
out entirely (observed in prod: "Strategy position creation timed out"),
especially while petrosa-data-manager is degraded. The WS FILLED event wins
that race essentially every time, so ``decision_id`` was always ``None`` and
the `filled` execution event was published-but-dropped by the data-manager
consumer (which requires a non-empty decision_id) — matching the prod
symptom of nonzero execution.events volume with zero fill-typed events.

Fix: ``Dispatcher._register_pending_fill_signal`` records
exchange_order_id -> Signal synchronously, with no I/O, the instant
``execute_order()`` returns an exchange order_id — well before
``create_strategy_position()`` starts its persistence calls. This map is
checked first in ``_on_user_data_fill``, with a fallback to the
strategy-position lookup and one short bounded retry for any residual
sub-second race.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def dispatcher():
    from tradeengine.dispatcher import Dispatcher

    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange_order_id_to_signal = {}
    d.order_to_signal = {}
    return d


def _make_signal(strategy_id: str = "rsi_reversal", decision_id: str | None = "dec-1"):
    sig = MagicMock()
    sig.strategy_id = strategy_id
    sig.decision_id = decision_id
    return sig


# ---------------------------------------------------------------------------
# _register_pending_fill_signal — synchronous registration, no I/O.
# ---------------------------------------------------------------------------


def test_register_pending_fill_signal_stores_by_exchange_order_id(dispatcher):
    order = MagicMock()
    order.order_id = "internal-1"
    dispatcher.order_to_signal["internal-1"] = _make_signal(decision_id="dec-A")

    dispatcher._register_pending_fill_signal(order, {"order_id": "exch-999"})

    assert "exch-999" in dispatcher.exchange_order_id_to_signal
    assert dispatcher.exchange_order_id_to_signal["exch-999"].decision_id == "dec-A"


def test_register_pending_fill_signal_noop_when_no_pending_signal(dispatcher):
    order = MagicMock()
    order.order_id = "internal-2"
    # No entry in order_to_signal for this order.
    dispatcher._register_pending_fill_signal(order, {"order_id": "exch-1"})
    assert dispatcher.exchange_order_id_to_signal == {}


def test_register_pending_fill_signal_noop_when_result_missing_order_id(dispatcher):
    order = MagicMock()
    order.order_id = "internal-3"
    dispatcher.order_to_signal["internal-3"] = _make_signal()
    dispatcher._register_pending_fill_signal(order, {"status": "error"})
    assert dispatcher.exchange_order_id_to_signal == {}


def test_register_pending_fill_signal_never_raises_on_bad_result(dispatcher):
    order = MagicMock()
    order.order_id = "internal-4"
    dispatcher.order_to_signal["internal-4"] = _make_signal()
    # result is not a dict at all — must be swallowed, not raised.
    dispatcher._register_pending_fill_signal(order, "not-a-dict")  # type: ignore[arg-type]
    assert dispatcher.exchange_order_id_to_signal == {}


def test_register_pending_fill_signal_fifo_eviction_bounds_growth(dispatcher):
    for i in range(510):
        order = MagicMock()
        order.order_id = f"internal-{i}"
        dispatcher.order_to_signal[f"internal-{i}"] = _make_signal()
        dispatcher._register_pending_fill_signal(order, {"order_id": f"exch-{i}"})

    assert len(dispatcher.exchange_order_id_to_signal) <= 500
    # Oldest entries evicted first (FIFO).
    assert "exch-0" not in dispatcher.exchange_order_id_to_signal
    assert "exch-509" in dispatcher.exchange_order_id_to_signal


# ---------------------------------------------------------------------------
# _consume_pending_fill_signal — cleanup once create_strategy_position lands.
# ---------------------------------------------------------------------------


def test_consume_pending_fill_signal_removes_entry(dispatcher):
    dispatcher.exchange_order_id_to_signal["exch-1"] = _make_signal()
    dispatcher._consume_pending_fill_signal({"order_id": "exch-1"})
    assert "exch-1" not in dispatcher.exchange_order_id_to_signal


def test_consume_pending_fill_signal_noop_when_absent(dispatcher):
    dispatcher._consume_pending_fill_signal({"order_id": "not-present"})
    assert dispatcher.exchange_order_id_to_signal == {}


def test_consume_pending_fill_signal_noop_when_result_none(dispatcher):
    dispatcher.exchange_order_id_to_signal["exch-1"] = _make_signal()
    dispatcher._consume_pending_fill_signal(None)
    assert "exch-1" in dispatcher.exchange_order_id_to_signal


# ---------------------------------------------------------------------------
# _on_user_data_fill — the actual race fix: signal map wins over the
# (possibly not-yet-populated) strategy_position lookup.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_user_data_fill_resolves_from_signal_map_before_strategy_position(
    dispatcher,
):
    """#546: the fast synchronous map must be consulted BEFORE the
    strategy_position_manager lookup, since strategy_positions may not be
    populated yet (create_strategy_position still in flight / timed out)."""
    order_obj = {
        "s": "BTCUSDT",
        "i": 42,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "50000",
        "z": "0.01",
    }
    dispatcher.exchange_order_id_to_signal["42"] = _make_signal(
        strategy_id="momentum", decision_id="dec-fast-path"
    )

    fake_spm = MagicMock()
    # If the fast path is broken, the code would fall through to this and
    # find nothing — asserting it is NOT consulted successfully proves the
    # fast path resolved first.
    fake_spm.get_strategy_position_by_entry_order_id.return_value = None

    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    pub.publish.assert_awaited_once()
    kw = pub.publish.await_args.kwargs
    assert kw["strategy_id"] == "momentum"
    assert kw["decision_id"] == "dec-fast-path"
    # No "no decision_id" warning should have been logged.
    for call in dispatcher.logger.warning.call_args_list:
        assert "has no decision_id" not in call.args[0]


@pytest.mark.asyncio
async def test_on_user_data_fill_consumes_signal_map_entry(dispatcher):
    """The map entry is single-use — a second fill callback for the same
    exchange order id must not resolve from a stale entry."""
    order_obj = {
        "s": "BTCUSDT",
        "i": 42,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "50000",
        "z": "0.01",
    }
    dispatcher.exchange_order_id_to_signal["42"] = _make_signal(decision_id="dec-once")

    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    assert "42" not in dispatcher.exchange_order_id_to_signal


@pytest.mark.asyncio
async def test_on_user_data_fill_falls_back_to_strategy_position_when_map_empty(
    dispatcher,
):
    """When the fast map has no entry (e.g. callback fires after
    create_strategy_position already completed and cleaned it up), the
    strategy_position_manager lookup still resolves decision_id."""
    order_obj = {
        "s": "ETHUSDT",
        "i": 7,
        "X": "FILLED",
        "S": "SELL",
        "o": "MARKET",
        "R": False,
        "L": "3000",
        "z": "1",
    }
    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = {
        "strategy_id": "breakout",
        "decision_id": "dec-slow-path",
    }

    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    kw = pub.publish.await_args.kwargs
    assert kw["strategy_id"] == "breakout"
    assert kw["decision_id"] == "dec-slow-path"


@pytest.mark.asyncio
async def test_on_user_data_fill_bounded_retry_resolves_residual_race(dispatcher):
    """#546: if neither source has data yet on the first check, one short
    bounded wait gives the exchange REST response a chance to land before
    giving up (rather than dropping decision_id permanently)."""
    order_obj = {
        "s": "BTCUSDT",
        "i": 99,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "50000",
        "z": "0.01",
    }
    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = None

    async def _populate_during_sleep(_seconds: float) -> None:
        dispatcher.exchange_order_id_to_signal["99"] = _make_signal(
            strategy_id="late_arrival", decision_id="dec-after-sleep"
        )

    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
        patch(
            "tradeengine.dispatcher.asyncio.sleep", side_effect=_populate_during_sleep
        ),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    kw = pub.publish.await_args.kwargs
    assert kw["strategy_id"] == "late_arrival"
    assert kw["decision_id"] == "dec-after-sleep"


@pytest.mark.asyncio
async def test_on_user_data_fill_still_warns_when_truly_unresolvable(dispatcher):
    """Both sources empty even after the bounded retry — the drop warning
    must still fire (defence in depth for genuinely orphaned fills)."""
    order_obj = {
        "s": "BTCUSDT",
        "i": 123,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "1",
        "z": "1",
    }
    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = None

    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
        patch("tradeengine.dispatcher.asyncio.sleep", new=AsyncMock()),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    dispatcher.logger.warning.assert_called()
    assert "has no decision_id" in dispatcher.logger.warning.call_args_list[0].args[0]
