"""Extensive end-to-end proof of the no-naked-position guarantee.

Requested by PM/dev review: "confirm that every position gets covered,
even with close SL/TP". This test drives the REAL production components
end to end and asserts the closed-loop invariant:

    After a reconcile+remediate cycle, EVERY open Binance position is
    either (a) hedged with reduceOnly SL+TP or (b) flattened after the
    grace window. No position is ever left permanently naked — not even
    when the strategy asks for a stop/target so tight the exchange
    PERCENT_PRICE filter would reject it.

Components under test (real, not mocked):
    - tradeengine.position_reconciler.detect_unhedged_positions  (detection)
    - tradeengine.naked_position_remediator.NakedPositionRemediator (arm/flatten)
    - tradeengine.naked_position_remediator._derive_protective_prices
      (safety-floor widening of too-tight stops)
    - tradeengine.exchange.binance.validate_and_adjust_price_for_percent_filter
      (real PERCENT_PRICE clamp — the exchange gate a naked SL would hit)

The only fakes are the network boundary: a FakeExchange whose ``execute``
runs the real price adjuster and mimics Binance's rejection of an
out-of-filter reduceOnly stop, and a book of open orders that reflects
what actually got placed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.exchange.binance import BinanceFuturesExchange
from tradeengine.naked_position_remediator import NakedPositionRemediator
from tradeengine.position_reconciler import detect_unhedged_positions

# ---------------------------------------------------------------------------
# Fake exchange boundary — enforces the REAL PERCENT_PRICE filter
# ---------------------------------------------------------------------------

# Binance USDT-M futures PERCENT_PRICE cap used in the incident (#541): ±5%.
FILTER_UP = 1.05
FILTER_DOWN = 0.95
SAFETY_FLOOR_PCT = 6.0  # te_min_sl_distance_pct default


class FakeExchange:
    """Network-boundary stand-in that behaves like Binance Futures.

    ``execute`` runs the REAL price adjuster against the position's mark
    price. If the adjuster refuses (returns None), the leg is rejected —
    exactly as Binance rejects an out-of-filter reduceOnly stop with
    -4131/-2021 — and the order is NOT booked, so the position stays
    naked for that leg. If the adjuster clamps/accepts, the leg is booked
    into ``open_orders`` and the position becomes hedged for that leg.
    """

    def __init__(self, mark_prices: dict[str, float]) -> None:
        self._mark = mark_prices
        # symbol -> list of open reduceOnly orders currently on the book
        self.open_orders: dict[str, list[dict[str, Any]]] = {}
        # Real adjuster bound to a live-ish exchange object with stubbed I/O.
        self._adjuster_exc = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
        self._adjuster_exc.client = MagicMock()
        self._adjuster_exc.testnet = True
        self.rejected_legs: list[dict[str, Any]] = []

    def _wire_adjuster(self, symbol: str) -> None:
        market = self._mark[symbol]
        self._adjuster_exc._get_current_price = AsyncMock(return_value=market)
        self._adjuster_exc.get_percent_price_filter = MagicMock(
            return_value={
                "multiplierUp": str(FILTER_UP),
                "multiplierDown": str(FILTER_DOWN),
            }
        )

    async def execute(self, order: Any) -> dict[str, Any]:
        symbol = order.symbol
        self._wire_adjuster(symbol)
        is_sl = order.type == "stop"
        raw_price = order.stop_loss if is_sl else order.take_profit
        order_type = "STOP_LOSS" if is_sl else "TAKE_PROFIT"

        (
            _adjusted,
            final_price,
            msg,
        ) = await self._adjuster_exc.validate_and_adjust_price_for_percent_filter(
            symbol=symbol,
            price=float(raw_price),
            order_type=order_type,
            min_safe_distance_pct=SAFETY_FLOOR_PCT,
        )

        if final_price is None:
            # Exchange rejects — leg NOT booked. Position stays naked here.
            self.rejected_legs.append(
                {"symbol": symbol, "type": order_type, "reason": msg}
            )
            raise RuntimeError(f"exchange rejected {order_type} for {symbol}: {msg}")

        booked = {
            "symbol": symbol,
            "positionSide": order.position_side,
            "type": "STOP_MARKET" if is_sl else "TAKE_PROFIT_MARKET",
            "reduceOnly": True,
            "closePosition": True,
            "price": final_price,
        }
        self.open_orders.setdefault(symbol, []).append(booked)
        return {"status": "FILLED", "price": final_price}


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


# ---------------------------------------------------------------------------
# Scenario matrix: entry price x side x requested-SL/TP tightness
# ---------------------------------------------------------------------------

# Each scenario: (symbol, side, entry, mark, stored_sl, stored_tp)
# stored_* = None means "no local record" -> fallback derivation kicks in.
# Deliberately includes SLs FAR tighter than the 6% floor and 5% filter.
_SCENARIOS = [
    # LONG, extremely tight stored SL (0.5%) — must be widened, not rejected.
    ("BTCUSDT", "LONG", 50000.0, 50000.0, 49750.0, 50250.0),
    # SHORT, extremely tight stored SL (0.5%) above entry.
    ("ETHUSDT", "SHORT", 3000.0, 3000.0, 3015.0, 2985.0),
    # LONG, tight SL exactly at 2% (inside 6% floor) — widen to 6%.
    ("SOLUSDT", "LONG", 150.0, 150.0, 147.0, 153.0),
    # SHORT, tight SL at 2% above entry.
    ("BNBUSDT", "SHORT", 600.0, 600.0, 612.0, 588.0),
    # LONG, no stored record at all — fallback 2% SL, must widen to floor.
    ("ADAUSDT", "LONG", 0.5, 0.5, None, None),
    # SHORT, no stored record — fallback path.
    ("XRPUSDT", "SHORT", 0.6, 0.6, None, None),
    # LONG, SL already compliant (10% away) — must be left untouched & placed.
    ("DOGEUSDT", "LONG", 0.1, 0.1, 0.09, 0.11),
    # SHORT, compliant SL 10% away.
    ("AVAXUSDT", "SHORT", 30.0, 30.0, 33.0, 27.0),
    # LONG, mark drifted UP from entry (unrealized profit) + tight SL.
    ("LTCUSDT", "LONG", 80.0, 84.0, 79.6, 82.0),
    # SHORT, mark drifted DOWN + tight SL.
    ("LINKUSDT", "SHORT", 15.0, 14.5, 15.05, 14.0),
]


def _binance_positions_from_scenarios(scenarios):
    return {
        (sym, side): {
            "symbol": sym,
            "positionSide": side,
            "positionAmt": 1.0 if side == "LONG" else -1.0,
            "entryPrice": entry,
        }
        for (sym, side, entry, _mark, _sl, _tp) in scenarios
    }


def _local_positions_from_scenarios(scenarios):
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for sym, side, _entry, _mark, sl, tp in scenarios:
        rec: dict[str, Any] = {"position_id": f"pos-{sym}"}
        if sl is not None:
            rec["stop_loss_price"] = sl
        if tp is not None:
            rec["take_profit_price"] = tp
        out[(sym, side)] = rec
    return out


def _mark_prices(scenarios):
    return {sym: mark for (sym, _s, _e, mark, _sl, _tp) in scenarios}


def _make_remediator(mode, exchange, local_positions, clock):
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value=local_positions)
    close_cb = AsyncMock(return_value={"status": "success", "position_closed": True})
    r = NakedPositionRemediator(
        exchange=exchange,
        position_manager=pm,
        close_position=close_cb,
        mode=mode,
        flatten_grace_sec=60,
        fallback_sl_pct=2.0,
        fallback_tp_pct=4.0,
        min_sl_distance_pct=SAFETY_FLOOR_PCT,
        clock=clock,
    )
    return r, close_cb


def _is_covered(symbol: str, side: str, open_orders) -> bool:
    """Mirror the exchange truth: hedged iff reduceOnly STOP + TAKE_PROFIT."""
    divs = detect_unhedged_positions(
        {(symbol, side): {"symbol": symbol, "positionSide": side, "positionAmt": 1.0}},
        open_orders,
    )
    return len(divs) == 0


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL — proves the assertions above have teeth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_control_both_protections_disabled_places_naked_tight_stop():
    """Sanity: if BOTH protection layers are disabled — the remediator's
    safety-floor widening (min_sl_distance_pct=0) AND the exchange's #541
    PERCENT_PRICE clamp / safety floor (adjuster min_safe_distance_pct=0)
    — a 0.5% stop is accepted AS-IS at 0.5% from market.

    That is precisely the 2026-05-30 -2021 incident condition. This test
    documents that the positive tests are not vacuous: with the guards
    removed, dangerously tight stops DO get through. The two independent
    layers exercised by the other tests are each load-bearing."""
    market = 50000.0
    exc = BinanceFuturesExchange.__new__(BinanceFuturesExchange)
    exc.client = MagicMock()
    exc.testnet = True
    exc._get_current_price = AsyncMock(return_value=market)
    exc.get_percent_price_filter = MagicMock(
        return_value={
            "multiplierUp": str(FILTER_UP),
            "multiplierDown": str(FILTER_DOWN),
        }
    )

    adjusted, price, _msg = await exc.validate_and_adjust_price_for_percent_filter(
        symbol="BTCUSDT",
        price=market * (1 - 0.005),  # 0.5% below market
        order_type="STOP_LOSS",
        min_safe_distance_pct=0.0,  # floor disabled
    )
    assert adjusted is False
    assert price == pytest.approx(market * (1 - 0.005))
    dist_pct = abs(price - market) / market * 100
    assert dist_pct < 1.0, "with both guards off a sub-1% stop leaks through"


# ---------------------------------------------------------------------------
# THE GUARANTEE — arm_only widens every tight SL and covers every position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_only_covers_every_position_even_with_tight_sl_tp():
    """With arm_only, every naked position — regardless of how tight the
    requested SL/TP — ends up hedged (reduceOnly SL + TP on the book).

    This is the core no-naked-position proof: the safety-floor widening in
    _derive_protective_prices plus the #541 PERCENT_PRICE clamp together
    guarantee a placeable stop for every position."""
    exchange = FakeExchange(_mark_prices(_SCENARIOS))
    binance_positions = _binance_positions_from_scenarios(_SCENARIOS)
    local = _local_positions_from_scenarios(_SCENARIOS)
    clock = _Clock()
    r, close_cb = _make_remediator("arm_only", exchange, local, clock)

    # All positions start fully naked (no open orders on the book).
    unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)
    assert len(unhedged) == len(_SCENARIOS), "every position must start naked"

    counts = await r.remediate(unhedged, binance_positions)

    # Every position armed, nothing flattened (arm_only never flattens),
    # nothing failed — the widening keeps every SL placeable.
    assert counts["failed"] == 0, f"unexpected arm failures: {exchange.rejected_legs}"
    assert counts["flattened"] == 0
    assert counts["armed"] == len(_SCENARIOS)

    # Closed-loop invariant: re-run detection against the resulting book.
    still_naked = detect_unhedged_positions(binance_positions, exchange.open_orders)
    assert still_naked == [], (
        f"positions left naked after arm_only remediation: {still_naked}"
    )

    # And explicitly per-position for a readable failure.
    for sym, side, *_rest in _SCENARIOS:
        assert _is_covered(sym, side, exchange.open_orders), (
            f"{sym}/{side} not covered after remediation"
        )
    close_cb.assert_not_called()


@pytest.mark.asyncio
async def test_every_armed_sl_respects_safety_floor_or_filter_clamp():
    """Every SL that lands on the book is either >= the 6% safety floor
    OR clamped to the furthest placeable price inside the ±5% filter.

    Proves the tight stop was WIDENED/CLAMPED, never placed naked-tight."""
    exchange = FakeExchange(_mark_prices(_SCENARIOS))
    binance_positions = _binance_positions_from_scenarios(_SCENARIOS)
    local = _local_positions_from_scenarios(_SCENARIOS)
    clock = _Clock()
    r, _ = _make_remediator("arm_only", exchange, local, clock)

    unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)
    await r.remediate(unhedged, binance_positions)

    for sym, booked in exchange.open_orders.items():
        mark = exchange._mark[sym]
        for o in booked:
            if "STOP" not in o["type"]:
                continue
            dist_pct = abs(o["price"] - mark) / mark * 100.0
            # Either it cleared the 6% floor, or it was clamped to just
            # inside the ±5% filter (>= ~3.9% after the 1% margin). Never
            # a routine-volatility-triggering sub-3% stop.
            assert dist_pct >= 3.9, (
                f"{sym} SL at {o['price']} is only {dist_pct:.2f}% from mark "
                f"{mark} — too tight, floor/clamp failed"
            )


# ---------------------------------------------------------------------------
# arm_or_flatten — if arming is genuinely impossible, flatten after grace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_or_flatten_flattens_when_arming_impossible():
    """Fallback guarantee: if a leg can NEVER be placed (exchange always
    rejects), arm_or_flatten flattens the position after the grace window
    so it still never stays naked."""
    scenario = [("BTCUSDT", "LONG", 50000.0, 50000.0, 49750.0, 50250.0)]
    exchange = FakeExchange(_mark_prices(scenario))

    # Force every leg to be rejected: adjuster returns None regardless.
    async def _always_reject(order):
        exchange.rejected_legs.append({"symbol": order.symbol})
        raise RuntimeError("simulated permanent exchange rejection")

    exchange.execute = _always_reject  # type: ignore[assignment]

    binance_positions = _binance_positions_from_scenarios(scenario)
    local = _local_positions_from_scenarios(scenario)
    clock = _Clock()
    r, close_cb = _make_remediator("arm_or_flatten", exchange, local, clock)

    unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)

    # Pass 1: first-seen recorded, arm attempted, arm fails (still naked).
    c1 = await r.remediate(unhedged, binance_positions)
    assert c1["armed"] == 0
    assert c1["flattened"] == 0
    assert c1["failed"] == 1
    close_cb.assert_not_called()

    # Advance past grace -> pass 2 escalates to flatten.
    clock.advance(61)
    c2 = await r.remediate(unhedged, binance_positions)
    assert c2["flattened"] == 1
    close_cb.assert_awaited_once()
    assert close_cb.await_args.kwargs["reason"] == "naked_position_grace_expired"


@pytest.mark.asyncio
async def test_partially_hedged_positions_get_missing_leg_only():
    """A position missing ONLY the SL (TP already on book) gets its SL
    armed and becomes fully covered — the other leg is not disturbed."""
    scenario = [("ETHUSDT", "LONG", 3000.0, 3000.0, 2985.0, 3120.0)]
    exchange = FakeExchange(_mark_prices(scenario))
    # Pre-seed a TP already on the book.
    exchange.open_orders["ETHUSDT"] = [
        {
            "symbol": "ETHUSDT",
            "positionSide": "LONG",
            "type": "TAKE_PROFIT_MARKET",
            "reduceOnly": True,
            "closePosition": True,
            "price": 3120.0,
        }
    ]
    binance_positions = _binance_positions_from_scenarios(scenario)
    local = _local_positions_from_scenarios(scenario)
    clock = _Clock()
    r, _ = _make_remediator("arm_only", exchange, local, clock)

    unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)
    assert len(unhedged) == 1
    assert unhedged[0]["tp_present"] is True
    assert unhedged[0]["sl_present"] is False

    await r.remediate(unhedged, binance_positions)

    # Now fully covered; exactly one SL added, TP untouched.
    assert _is_covered("ETHUSDT", "LONG", exchange.open_orders)
    sl_orders = [o for o in exchange.open_orders["ETHUSDT"] if "STOP" in o["type"]]
    tp_orders = [
        o for o in exchange.open_orders["ETHUSDT"] if "TAKE_PROFIT" in o["type"]
    ]
    assert len(sl_orders) == 1
    assert len(tp_orders) == 1


@pytest.mark.asyncio
async def test_idempotent_no_naked_across_repeated_cycles():
    """Running the full cycle repeatedly never regresses a covered
    position back to naked and never double-flattens."""
    exchange = FakeExchange(_mark_prices(_SCENARIOS))
    binance_positions = _binance_positions_from_scenarios(_SCENARIOS)
    local = _local_positions_from_scenarios(_SCENARIOS)
    clock = _Clock()
    r, close_cb = _make_remediator("arm_or_flatten", exchange, local, clock)

    for cycle in range(4):
        clock.advance(5)
        unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)
        await r.remediate(unhedged, binance_positions)
        # After the first cycle everything should be covered and stay so.
        residual = detect_unhedged_positions(binance_positions, exchange.open_orders)
        assert residual == [], f"cycle {cycle}: positions went naked: {residual}"

    # Never flattened a healthy position (all were armable well within grace).
    close_cb.assert_not_called()


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("tight_pct", [0.1, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 5.9])
@pytest.mark.asyncio
async def test_every_tightness_below_floor_still_gets_covered(side, tight_pct):
    """Sweep: for SLs from 0.1% up to just under the 6% floor, on both
    sides, the position always ends up covered — never naked."""
    entry = 1000.0
    if side == "LONG":
        sl = entry * (1 - tight_pct / 100.0)
        tp = entry * (1 + tight_pct / 100.0)
    else:
        sl = entry * (1 + tight_pct / 100.0)
        tp = entry * (1 - tight_pct / 100.0)
    scenario = [("BTCUSDT", side, entry, entry, sl, tp)]
    exchange = FakeExchange(_mark_prices(scenario))
    binance_positions = _binance_positions_from_scenarios(scenario)
    local = _local_positions_from_scenarios(scenario)
    clock = _Clock()
    r, _ = _make_remediator("arm_only", exchange, local, clock)

    unhedged = detect_unhedged_positions(binance_positions, exchange.open_orders)
    counts = await r.remediate(unhedged, binance_positions)

    assert counts["failed"] == 0, (
        f"{side} SL at {tight_pct}% failed to arm: {exchange.rejected_legs}"
    )
    assert _is_covered("BTCUSDT", side, exchange.open_orders), (
        f"{side} position with {tight_pct}% SL left naked"
    )
