"""Position-side-aware protective-order direction guard (#479).

For a LONG position, the stop-loss must sit BELOW entry (closes when price falls)
and the take-profit must sit ABOVE entry (closes in profit when price rises).
For a SHORT the directions are reversed. Strategy signals occasionally arrive
with wrong-side absolute prices (observed 2026-06-18 across BNB/LTC/LINK LONGs
where SLs were quoted at +3.5% above entry); without correction those orders
either get rejected by Binance with ``APIError(-2021)`` or — if the price slips
inside the PERCENT_PRICE band — would trigger immediately and exit the position
for a loss.

This module exposes a pure function that returns a direction-correct protective
price plus a metadata payload describing what was done. Callers are responsible
for resolving the reference price (entry → market fallback) and for fetching the
current market price; this keeps the helper synchronous and trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PositionSide = Literal["LONG", "SHORT"]
ProtectiveKind = Literal["SL", "TP"]


@dataclass(frozen=True)
class DirectionCorrection:
    """Result of a direction check.

    Attributes:
        price: The price to actually submit to the exchange (corrected if needed).
        was_corrected: True if the input price was on the wrong side and got mirrored.
        reason: Short human-readable string; "" when no correction was needed.
        original_price: The input price prior to any correction.
    """

    price: float
    was_corrected: bool
    reason: str
    original_price: float


def _required_side(kind: ProtectiveKind, position_side: PositionSide) -> int:
    """Return +1 if the protective price must be ABOVE reference, -1 if BELOW."""
    if kind == "SL":
        return -1 if position_side == "LONG" else +1
    # TP
    return +1 if position_side == "LONG" else -1


def correct_protective_price(
    *,
    kind: ProtectiveKind,
    position_side: PositionSide,
    requested_price: float,
    requested_pct: float | None,
    reference_price: float,
    min_distance_pct: float,
) -> DirectionCorrection:
    """Return a direction-correct protective price.

    Args:
        kind: "SL" or "TP".
        position_side: "LONG" or "SHORT".
        requested_price: The protective price the caller wants to submit.
        requested_pct: The percentage distance hint, if provided by upstream.
            Used to recompute exactly when present and >0; otherwise the function
            mirrors ``requested_price`` across ``reference_price`` using the
            implied distance ``abs(requested - reference)/reference``.
        reference_price: Entry price if known and >0, else the current market.
            MUST be > 0 — callers are responsible for falling back.
        min_distance_pct: Minimum |distance| from reference (as a fraction, e.g. 0.06).
            The corrected price is clamped to at least this distance on the
            required side. The floor only matters for SL placements; pass 0
            for TP if you don't want a floor.

    Returns:
        DirectionCorrection describing the outcome.

    Raises:
        ValueError: If ``reference_price`` is not strictly positive.
    """
    if reference_price <= 0:
        raise ValueError(
            "reference_price must be > 0 — callers must resolve entry→market fallback"
        )
    sign = _required_side(kind, position_side)

    required_floor = reference_price * (1 + sign * min_distance_pct)
    on_correct_side = (sign > 0 and requested_price > reference_price) or (
        sign < 0 and requested_price < reference_price
    )

    if on_correct_side:
        # On the correct side already — leave the price alone. Strategy-level
        # minimum-distance enforcement happens upstream (the dispatcher's
        # MIN_SL_DISTANCE_PCT floor); the binance.py safety-floor check (#424)
        # remains the authoritative second gate that refuses too-close stops.
        # We intentionally do not double-clamp here.
        _ = required_floor  # kept for symmetry/debug; not used on correct-side path
        return DirectionCorrection(
            price=requested_price,
            was_corrected=False,
            reason="",
            original_price=requested_price,
        )

    if requested_pct is not None and requested_pct > 0:
        distance_pct = max(requested_pct, min_distance_pct)
        corrected = reference_price * (1 + sign * distance_pct)
        reason = (
            f"{position_side} {kind} requested {requested_price:.6f} on wrong side of "
            f"reference {reference_price:.6f}; recomputed to {corrected:.6f} using "
            f"pct={distance_pct * 100:.2f}%"
        )
        return DirectionCorrection(
            price=corrected,
            was_corrected=True,
            reason=reason,
            original_price=requested_price,
        )

    implied_pct = abs(requested_price - reference_price) / reference_price
    effective_pct = max(implied_pct, min_distance_pct)
    corrected = reference_price * (1 + sign * effective_pct)
    reason = (
        f"{position_side} {kind} requested {requested_price:.6f} on wrong side of "
        f"reference {reference_price:.6f} and no pct hint; mirrored across "
        f"reference to {corrected:.6f} (implied {implied_pct * 100:.2f}%, "
        f"applied {effective_pct * 100:.2f}% after floor)"
    )
    return DirectionCorrection(
        price=corrected,
        was_corrected=True,
        reason=reason,
        original_price=requested_price,
    )
