"""Adversarial tests for tradeengine#505 — SHORT position sign inconsistency.

Two related hazards surfaced during the 2026-07-16 incident:

1. Storage: ``StrategyPositionManager`` stores ``entry_quantity`` as the raw,
   UNSIGNED amount (strategy_position_manager.py:153) with ``side`` tracked
   separately. A SHORT can therefore be recorded as
   ``side="SHORT", quantity=+9470.2``.

2. Matching / inference: the one-way (``BOTH``) branch of
   ``_has_matching_exchange_position`` (strategy_position_reconciler.py:99-104)
   assumes a SIGNED exchange quantity — ``side == "SHORT" and snap.quantity < 0``.
   Live Binance data in the incident showed a SHORT snapshot carrying a
   POSITIVE ``positionAmt`` (9470.2). If such a snapshot is ever presented in
   one-way mode, a REAL short fails to match its strategy row → the reconciler
   mis-classifies a live, real position as a ghost and could evict it.

These tests assert robust behavior: side matching must be driven by the
explicit side / consistent sign convention, not by an assumed sign that live
data violates. xfail(strict) for the hazards that require the fix.
"""

from __future__ import annotations

from tradeengine.exchange_truth_store import PositionSnapshot
from tradeengine.strategy_position_reconciler import _has_matching_exchange_position


def _short_strategy_row() -> dict:
    return {"symbol": "XRPUSDT", "side": "SHORT", "quantity": 9470.2}


class TestHedgeModeMatch:
    """Hedge mode keys directly by (symbol, side) — should always match."""

    def test_short_hedge_key_matches_regardless_of_sign(self) -> None:
        # Incident-shape: hedge SHORT snapshot with POSITIVE quantity.
        ex = {
            ("XRPUSDT", "SHORT"): PositionSnapshot(
                symbol="XRPUSDT",
                side="SHORT",
                quantity=9470.2,  # positive, as live Binance returned
                entry_price=1.0945,
                unrealized_pnl=-71.0,
            )
        }
        assert _has_matching_exchange_position(_short_strategy_row(), ex) is True


class TestOneWayModeSignHazard:
    """One-way (BOTH) matching assumes signed qty; live data can violate it."""

    def test_oneway_short_with_negative_qty_matches(self) -> None:
        """Baseline: properly-signed one-way SHORT matches (documents intent)."""
        ex = {
            ("XRPUSDT", "BOTH"): PositionSnapshot(
                symbol="XRPUSDT",
                side="BOTH",
                quantity=-9470.2,  # correctly signed short
                entry_price=1.0945,
                unrealized_pnl=-71.0,
            )
        }
        assert _has_matching_exchange_position(_short_strategy_row(), ex) is True

    def test_oneway_short_with_positive_qty_still_matches(self) -> None:
        """The incident hazard: a real short presented with positive qty in a
        BOTH snapshot must NOT be treated as absent (ghost)."""
        ex = {
            ("XRPUSDT", "BOTH"): PositionSnapshot(
                symbol="XRPUSDT",
                side="BOTH",
                quantity=9470.2,  # POSITIVE — the sign hazard
                entry_price=1.0945,
                unrealized_pnl=-71.0,
            )
        }
        # A live position must never be misclassified as a ghost due to sign.
        assert _has_matching_exchange_position(_short_strategy_row(), ex) is True


class TestStorageSignConvention:
    """The strategy store should carry a consistent, documented sign convention
    so downstream sign-based inference is safe."""

    def test_storage_currently_stores_unsigned_quantity(self) -> None:
        """Documents the bug at source: create_strategy_position stores the raw
        ``execution_result['amount']`` as ``entry_quantity`` with no sign applied,
        keying the sign convention entirely on the separate ``side`` field. Passes
        today; if it fails the fix may have introduced a signed convention."""
        import inspect

        from tradeengine.strategy_position_manager import StrategyPositionManager

        src = inspect.getsource(StrategyPositionManager.create_strategy_position)
        assert 'entry_quantity = float(execution_result.get("amount"' in src, (
            "entry_quantity derivation changed — re-verify the sign convention (#505)"
        )
        # No negation / sign application on the SHORT branch today.
        assert "-entry_quantity" not in src and "abs(entry_quantity)" not in src

    def test_storage_applies_signed_convention_for_short(self) -> None:
        """Post-fix: the storage path must encode SHORT with a negative signed
        quantity (or otherwise guarantee sign-consistent inference)."""
        import inspect

        from tradeengine.strategy_position_manager import StrategyPositionManager

        src = inspect.getsource(StrategyPositionManager.create_strategy_position)
        # Expect the fix to introduce an explicit signed quantity for SHORT.
        assert "-entry_quantity" in src or "signed_quantity" in src, (
            "No signed-quantity convention applied for SHORT storage (#505)"
        )
