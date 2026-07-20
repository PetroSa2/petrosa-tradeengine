"""End-to-end integration test for the orphan / naked-position lifecycle.

Implements PetroSa2/petrosa-tradeengine#517.

Individual components (OCOManager, PositionReconciler, NakedPositionRemediator)
have unit tests, but there is no integration test that exercises the *full*
orphan lifecycle across component boundaries:

    signal-driven entry fills
        -> OCO placement partially fails (one leg fails, surviving leg cancelled)
        -> position is now naked on the exchange (no SL+TP)
        -> the next PositionReconciler cycle detects the unhedged position
        -> the NakedPositionRemediator (arm_only mode) re-arms the missing leg(s)
        -> final state: position hedged again

This module mocks *only* at the Binance API boundary (the exchange's async
``execute`` / ``get_position_info`` / ``get_open_algo_orders`` methods and the
raw ``.client`` REST surface). Everything above that boundary — OCOManager,
PositionManager, PositionReconciler, NakedPositionRemediator — runs for real so
wiring bugs between them are caught.

Patterns follow ``tests/test_oco_integration.py`` and
``tests/test_adversarial_504_partial_oco_naked.py``.

Run with:
    uv run pytest tests/test_e2e_orphan_lifecycle.py -v
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager
from tradeengine.naked_position_remediator import NakedPositionRemediator
from tradeengine.position_manager import PositionManager
from tradeengine.position_reconciler import PositionReconciler

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Test doubles — Binance API boundary only
# ---------------------------------------------------------------------------


def _logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _position_info(symbol: str, side: str, qty: float, entry: float) -> dict[str, Any]:
    """Shape a raw Binance positionRisk record (as returned by
    ``exchange.get_position_info()``)."""
    signed_qty = qty if side == "LONG" else -qty
    return {
        "symbol": symbol,
        "positionSide": side,
        "positionAmt": str(signed_qty),
        "entryPrice": str(entry),
    }


def _algo_order(
    symbol: str, side: str, order_type: str, algo_id: str
) -> dict[str, Any]:
    """Shape a raw Binance open reduceOnly algo order (SL or TP leg)."""
    return {
        "symbol": symbol,
        "positionSide": side,
        "type": order_type,  # e.g. "STOP_MARKET" / "TAKE_PROFIT_MARKET"
        "reduceOnly": True,
        "algoId": algo_id,
    }


def _make_partial_oco_exchange(sl_ok: bool, tp_ok: bool) -> AsyncMock:
    """Exchange whose OCO placement fails one leg.

    ``execute`` returns an ``order_id`` for the OK leg and ``None`` for the
    failing leg. The surviving-leg cancel goes through
    ``exchange.client._request_futures_api`` — a plain MagicMock so the
    OCOManager's atomic-cancel path succeeds without a real network call.
    """
    exch = AsyncMock()
    exch.client = MagicMock()
    sl_id, tp_id = "1000000091274545", "1000000091274546"

    async def execute(order: Any) -> dict[str, Any]:
        if str(order.type) in (
            "OrderType.STOP",
            "stop",
            "STOP",
            "OrderType.STOP_MARKET",
        ):
            return {
                "order_id": sl_id if sl_ok else None,
                "status": "NEW" if sl_ok else "failed",
            }
        return {
            "order_id": tp_id if tp_ok else None,
            "status": "NEW" if tp_ok else "failed",
        }

    exch.execute = AsyncMock(side_effect=execute)
    return exch


class ReconcileExchange:
    """Exchange double for the reconciliation / remediation half of the flow.

    Models the exchange as ground truth: it reports open positions via
    ``get_position_info`` and open reduceOnly algo orders via
    ``get_open_algo_orders``. A successful re-arm (``execute`` of a STOP /
    TAKE_PROFIT reduceOnly order) mutates the in-memory order book so the
    *next* reconciliation pass sees the position as hedged — proving the
    lifecycle closes.
    """

    def __init__(
        self,
        positions: list[dict[str, Any]],
        orders_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._positions = positions
        self.orders_by_symbol: dict[str, list[dict[str, Any]]] = orders_by_symbol or {}
        self.client = MagicMock()
        self.executed: list[Any] = []

    async def get_position_info(self) -> list[dict[str, Any]]:
        return list(self._positions)

    async def get_open_algo_orders(
        self, symbol: str | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        if symbol is None:
            return [o for orders in self.orders_by_symbol.values() for o in orders]
        return list(self.orders_by_symbol.get(symbol, []))

    async def execute(self, order: Any) -> dict[str, Any]:
        """Record the re-arm order and add it to the exchange order book."""
        self.executed.append(order)
        o_type = str(order.type).upper()
        if "STOP" in o_type:
            binance_type = "STOP_MARKET"
            algo_id = f"rearm-sl-{order.symbol}"
        else:
            binance_type = "TAKE_PROFIT_MARKET"
            algo_id = f"rearm-tp-{order.symbol}"
        side = order.position_side or "LONG"
        self.orders_by_symbol.setdefault(order.symbol, []).append(
            _algo_order(order.symbol, side, binance_type, algo_id)
        )
        return {"order_id": algo_id, "status": "NEW"}


# ---------------------------------------------------------------------------
# AC1: Full orphan lifecycle — detection to remediation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_full_orphan_lifecycle_detection_to_remediation() -> None:
    """AC1: signal entry -> OCO partial fail -> naked -> reconciler detects ->
    arm_only remediator re-arms missing leg -> final state hedged."""
    symbol, side = "BTCUSDT", "LONG"
    qty, entry = 0.01, 50000.0

    # --- Stage 1: MARKET entry filled; OCO placement partially fails ---
    # SL posts, TP fails -> surviving SL cancelled -> position naked.
    oco_exch = _make_partial_oco_exchange(sl_ok=True, tp_ok=False)
    oco = OCOManager(exchange=oco_exch, logger=_logger("ac1-oco"))
    try:
        oco_result = await oco.place_oco_orders(
            position_id="pos-btc",
            symbol=symbol,
            position_side=side,
            quantity=qty,
            stop_loss_price=49000.0,
            take_profit_price=52000.0,
            entry_price=entry,
        )
    finally:
        await oco.stop_monitoring()

    # The OCO layer must signal the position is naked and needs remediation.
    assert oco_result["status"] == "failed"
    assert oco_result.get("position_naked") is True
    assert oco_result.get("requires_remediation") is True
    assert oco_result.get("escalate") is True
    # Surviving leg (SL) cancelled exactly once for atomicity.
    assert oco_exch.client._request_futures_api.call_count == 1

    # --- Stage 2: position now naked on exchange; reconciler + remediator ---
    # Exchange truth: one open position, NO reduceOnly SL/TP orders.
    recon_exch = ReconcileExchange(
        positions=[_position_info(symbol, side, qty, entry)],
        orders_by_symbol={symbol: []},
    )
    pm = PositionManager(exchange=recon_exch)
    # Seed the local strategy SL/TP so re-arm matches strategy intent.
    pm.positions[(symbol, side)] = {
        "symbol": symbol,
        "position_side": side,
        "quantity": qty,
        "stop_loss_price": 49000.0,
        "take_profit_price": 52000.0,
    }

    remediator = NakedPositionRemediator(
        exchange=recon_exch,
        position_manager=pm,
        close_position=AsyncMock(),
        mode="arm_only",
    )
    reconciler = PositionReconciler(
        exchange=recon_exch,
        position_manager=pm,
        remediator=remediator,
    )

    # First cycle: detect naked position + re-arm the missing legs.
    divergences = await reconciler.reconcile_once()
    unhedged = [d for d in divergences if d["category"] == "unhedged"]
    assert len(unhedged) == 1, f"expected 1 unhedged divergence, got {divergences}"
    assert unhedged[0]["symbol"] == symbol
    assert unhedged[0]["side"] == side
    # The position had NO orders, so both legs were missing.
    assert unhedged[0]["sl_present"] is False
    assert unhedged[0]["tp_present"] is False

    # Remediator (arm_only) must have executed the missing legs.
    assert recon_exch.executed, "remediator did not re-arm any leg"
    rearm_types = {str(o.type).upper() for o in recon_exch.executed}
    assert any("STOP" in t for t in rearm_types), "SL leg not re-armed"
    assert any("TAKE_PROFIT" in t for t in rearm_types), "TP leg not re-armed"
    # arm_only never flattens.
    assert all(o.reduce_only for o in recon_exch.executed)

    # --- Stage 3: final state — position hedged with both SL and TP ---
    final_divergences = await reconciler.reconcile_once()
    final_unhedged = [d for d in final_divergences if d["category"] == "unhedged"]
    assert final_unhedged == [], (
        f"position should be hedged after remediation, still unhedged: {final_unhedged}"
    )


# ---------------------------------------------------------------------------
# AC2: Orphan survives pod restart cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_orphan_survives_pod_restart() -> None:
    """AC2: create orphan via partial OCO failure, then simulate a restart with
    a fresh OCOManager + PositionReconciler. The fresh OCOManager's
    reconcile_from_exchange() must discover the orphaned (unpaired) order, and
    the fresh reconciler must flag the naked position for remediation."""
    symbol, side = "ETHUSDT", "LONG"
    qty, entry = 0.5, 3000.0

    # --- Create the orphan: partial OCO failure leaves the position naked ---
    oco_exch = _make_partial_oco_exchange(sl_ok=False, tp_ok=True)
    oco = OCOManager(exchange=oco_exch, logger=_logger("ac2-oco-pre"))
    try:
        pre_result = await oco.place_oco_orders(
            position_id="pos-eth",
            symbol=symbol,
            position_side=side,
            quantity=qty,
            stop_loss_price=2900.0,
            take_profit_price=3200.0,
            entry_price=entry,
        )
    finally:
        await oco.stop_monitoring()
    assert pre_result.get("requires_remediation") is True
    # TP posted, SL failed -> surviving TP cancelled.
    assert pre_result.get("cancelled_leg") == "TP"

    # --- Simulate pod restart: fresh managers, no in-memory OCO state ---
    # The exchange still shows the position and one *unpaired* leftover leg
    # (an orphaned SL algo order from a prior lifecycle that never got a TP).
    orphan_algo_id = "orphan-sl-9999"
    restart_exch = ReconcileExchange(
        positions=[_position_info(symbol, side, qty, entry)],
        orders_by_symbol={
            symbol: [_algo_order(symbol, side, "STOP_MARKET", orphan_algo_id)]
        },
    )
    # Fresh OCOManager after "restart" starts with empty active_oco_pairs.
    fresh_oco = OCOManager(exchange=restart_exch, logger=_logger("ac2-oco-post"))
    assert fresh_oco.active_oco_pairs == {}

    pairs_rebuilt = await fresh_oco.reconcile_from_exchange()
    # The single unpaired SL leg is discovered and registered as orphaned.
    assert pairs_rebuilt >= 1
    registered = [
        entry_ for entries in fresh_oco.active_oco_pairs.values() for entry_ in entries
    ]
    assert any(e.get("orphaned") for e in registered), (
        f"orphaned leg not registered: {fresh_oco.active_oco_pairs}"
    )

    # Fresh reconciler must still flag the position as naked (only SL present,
    # TP missing) so remediation is triggered post-restart.
    pm = PositionManager(exchange=restart_exch)
    fresh_reconciler = PositionReconciler(
        exchange=restart_exch,
        position_manager=pm,
    )
    divergences = await fresh_reconciler.reconcile_once()
    unhedged = [d for d in divergences if d["category"] == "unhedged"]
    assert len(unhedged) == 1
    assert unhedged[0]["sl_present"] is True
    assert unhedged[0]["tp_present"] is False


# ---------------------------------------------------------------------------
# AC3: Multiple orphans handled concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_multiple_orphans_handled_concurrently() -> None:
    """AC3: 3 naked positions (BTC, ETH, SOL) simultaneously. All 3 detected in
    a single reconciliation pass and all 3 remediated independently with no
    cross-contamination."""
    specs = [
        ("BTCUSDT", "LONG", 0.01, 50000.0),
        ("ETHUSDT", "LONG", 0.5, 3000.0),
        ("SOLUSDT", "SHORT", 10.0, 150.0),
    ]

    recon_exch = ReconcileExchange(
        positions=[_position_info(s, side, q, e) for (s, side, q, e) in specs],
        # All three positions start naked (no reduceOnly SL/TP).
        orders_by_symbol={s: [] for (s, _side, _q, _e) in specs},
    )
    pm = PositionManager(exchange=recon_exch)
    for symbol, side, qty, _entry in specs:
        pm.positions[(symbol, side)] = {
            "symbol": symbol,
            "position_side": side,
            "quantity": qty,
        }

    remediator = NakedPositionRemediator(
        exchange=recon_exch,
        position_manager=pm,
        close_position=AsyncMock(),
        mode="arm_only",
    )
    reconciler = PositionReconciler(
        exchange=recon_exch,
        position_manager=pm,
        remediator=remediator,
    )

    # Single reconciliation pass detects all three.
    divergences = await reconciler.reconcile_once()
    unhedged = [d for d in divergences if d["category"] == "unhedged"]
    detected_symbols = {d["symbol"] for d in unhedged}
    assert detected_symbols == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}, (
        f"not all orphans detected in one pass: {detected_symbols}"
    )
    assert len(unhedged) == 3

    # All three remediated independently — each got both legs re-armed with
    # the correct reduce-only side (SELL for LONG, BUY for SHORT).
    rearmed_by_symbol: dict[str, set[str]] = {}
    for order in recon_exch.executed:
        rearmed_by_symbol.setdefault(order.symbol, set()).add(str(order.type).upper())

    for symbol, side, _qty, _entry in specs:
        types = rearmed_by_symbol.get(symbol, set())
        assert any("STOP" in t for t in types), f"{symbol} SL not re-armed"
        assert any("TAKE_PROFIT" in t for t in types), f"{symbol} TP not re-armed"
        expected_side = "sell" if side == "LONG" else "buy"
        symbol_orders = [o for o in recon_exch.executed if o.symbol == symbol]
        assert all(
            str(o.side).lower().endswith(expected_side) for o in symbol_orders
        ), f"{symbol} re-arm used wrong order side (expected {expected_side})"

    # No cross-contamination: after remediation, every position is hedged.
    final_divergences = await reconciler.reconcile_once()
    final_unhedged = [d for d in final_divergences if d["category"] == "unhedged"]
    assert final_unhedged == [], f"positions still naked: {final_unhedged}"
