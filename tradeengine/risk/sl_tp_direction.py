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

import logging
from dataclasses import dataclass
from typing import Any, Literal

protective_price_mirror_clamped_total: Any = None
try:  # pragma: no cover - metrics are optional in unit-test contexts
    from prometheus_client import Counter

    protective_price_mirror_clamped_total = Counter(
        "tradeengine_protective_price_mirror_clamped_total",
        "Times a wrong-side protective price implied an absurd mirror distance "
        "(> MAX_PLAUSIBLE_DISTANCE_PCT) and was clamped to the safe floor "
        "instead of being mirrored to ~2x reference (#502).",
        ["kind", "position_side"],
    )
except Exception:  # pragma: no cover - prometheus not importable
    protective_price_mirror_clamped_total = None

logger = logging.getLogger(__name__)

PositionSide = Literal["LONG", "SHORT"]
ProtectiveKind = Literal["SL", "TP"]

# Maximum plausible protective-order distance from the reference price. A
# wrong-side absolute price that implies a mirror distance beyond this bound is
# broken input (garbage / far-wrong-side), NOT a legitimate stop — mirroring it
# produces ~2x/4x reference (BCHUSDT 2026-07-16: $223.66 -> $454.66, +103%,
# Binance -2021). When the implied distance exceeds this cap we clamp to the
# safe configured floor (``min_distance_pct``) rather than mirror to an absurd
# level. Tied to the widest PERCENT_PRICE band the exchange will plausibly
# accept for a stop. See tradeengine#502.
MAX_PLAUSIBLE_DISTANCE_PCT = 0.20  # 20%


@dataclass(frozen=True)
class DirectionCorrection:
    """Result of a direction check.

    Attributes:
        price: The price to actually submit to the exchange (corrected if needed).
        was_corrected: True if the input price was on the wrong side and got mirrored.
        reason: Short human-readable string; "" when no correction was needed.
        original_price: The input price prior to any correction.
        was_clamped: True if the implied mirror distance exceeded
            ``MAX_PLAUSIBLE_DISTANCE_PCT`` and the correction was clamped to the
            safe floor instead of mirrored to an absurd level (#502).
    """

    price: float
    was_corrected: bool
    reason: str
    original_price: float
    was_clamped: bool = False


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
        correct_side_distance = abs(requested_price - reference_price) / reference_price
        if correct_side_distance > MAX_PLAUSIBLE_DISTANCE_PCT:
            # Correct side but absurdly far (e.g. a $0.01 SL against a $223.66
            # reference — 100% away). Such a price is broken input: the exchange
            # rejects it (PERCENT_PRICE / -2021) or it can never realistically
            # trigger. Clamp it back to the widest plausible distance on the
            # correct side rather than shipping garbage (#502).
            corrected = reference_price * (1 + sign * MAX_PLAUSIBLE_DISTANCE_PCT)
            reason = (
                f"{position_side} {kind} requested {requested_price:.6f} on the correct "
                f"side of reference {reference_price:.6f} but {correct_side_distance * 100:.2f}% "
                f"away — exceeds max plausible {MAX_PLAUSIBLE_DISTANCE_PCT * 100:.2f}%; "
                f"CLAMPED to {corrected:.6f} (#502)"
            )
            logger.warning("⚠️ SL/TP far-side clamp (#502): %s", reason)
            if protective_price_mirror_clamped_total is not None:
                try:  # pragma: no cover - metric side-effect
                    protective_price_mirror_clamped_total.labels(
                        kind=kind, position_side=position_side
                    ).inc()
                except Exception:  # pragma: no cover
                    pass
            return DirectionCorrection(
                price=corrected,
                was_corrected=True,
                reason=reason,
                original_price=requested_price,
                was_clamped=True,
            )
        # On the correct side and within the plausible band — leave the price
        # alone. Strategy-level minimum-distance enforcement happens upstream
        # (the dispatcher's MIN_SL_DISTANCE_PCT floor); the binance.py
        # safety-floor check (#424) remains the authoritative second gate that
        # refuses too-close stops. We intentionally do not double-clamp the floor.
        _ = required_floor  # kept for symmetry/debug; not used on correct-side path
        return DirectionCorrection(
            price=requested_price,
            was_corrected=False,
            reason="",
            original_price=requested_price,
        )

    if requested_pct is not None and requested_pct > 0:
        distance_pct = max(requested_pct, min_distance_pct)
        # Defense-in-depth (#502): a legitimate SL/TP pct hint sits well inside
        # the plausible band; anything beyond MAX_PLAUSIBLE_DISTANCE_PCT is a
        # broken hint and must not manufacture an absurd price either.
        pct_clamped = distance_pct > MAX_PLAUSIBLE_DISTANCE_PCT
        if pct_clamped:
            distance_pct = MAX_PLAUSIBLE_DISTANCE_PCT
            if protective_price_mirror_clamped_total is not None:
                try:  # pragma: no cover - metric side-effect
                    protective_price_mirror_clamped_total.labels(
                        kind=kind, position_side=position_side
                    ).inc()
                except Exception:  # pragma: no cover
                    pass
        corrected = reference_price * (1 + sign * distance_pct)
        reason = (
            f"{position_side} {kind} requested {requested_price:.6f} on wrong side of "
            f"reference {reference_price:.6f}; recomputed to {corrected:.6f} using "
            f"pct={distance_pct * 100:.2f}%"
            + (" (clamped to max plausible, #502)" if pct_clamped else "")
        )
        if pct_clamped:
            logger.warning("⚠️ SL/TP pct-hint clamp (#502): %s", reason)
        return DirectionCorrection(
            price=corrected,
            was_corrected=True,
            reason=reason,
            original_price=requested_price,
            was_clamped=pct_clamped,
        )

    implied_pct = abs(requested_price - reference_price) / reference_price

    if implied_pct > MAX_PLAUSIBLE_DISTANCE_PCT:
        # Garbage / far-wrong-side input: the implied distance is meaningless, so
        # mirroring it would manufacture an absurd ~2x/4x price that either
        # exits the position immediately or is rejected by Binance with -2021
        # (BCHUSDT 2026-07-16). Clamp to the safe configured floor instead of
        # trusting the implied magnitude (#502).
        effective_pct = max(min_distance_pct, 0.0)
        corrected = reference_price * (1 + sign * effective_pct)
        reason = (
            f"{position_side} {kind} requested {requested_price:.6f} on wrong side of "
            f"reference {reference_price:.6f} with no pct hint; implied distance "
            f"{implied_pct * 100:.2f}% exceeds max plausible "
            f"{MAX_PLAUSIBLE_DISTANCE_PCT * 100:.2f}% — CLAMPED to safe floor "
            f"{effective_pct * 100:.2f}% -> {corrected:.6f} (refused mirror-to-2x, #502)"
        )
        logger.warning("⚠️ SL/TP mirror clamp (#502): %s", reason)
        if protective_price_mirror_clamped_total is not None:
            try:  # pragma: no cover - metric side-effect
                protective_price_mirror_clamped_total.labels(
                    kind=kind, position_side=position_side
                ).inc()
            except Exception:  # pragma: no cover - never let metrics break placement
                pass
        return DirectionCorrection(
            price=corrected,
            was_corrected=True,
            reason=reason,
            original_price=requested_price,
            was_clamped=True,
        )

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


@dataclass(frozen=True)
class MarketSideDecision:
    """Outcome of validating a stop-loss against the LIVE market price (#551).

    ``correct_protective_price`` guarantees a stop is on the correct side of the
    ENTRY price. That is necessary but not sufficient: once the market crosses
    the entry (position underwater), an entry-side-correct stop can sit on the
    WRONG side of the *current* market — Binance then rejects it with
    ``APIError(-2021)`` "Order would immediately trigger", the OCO cancels the
    surviving leg, and the position is left NAKED (XLMUSDT SHORT 2026-08-20).

    A stop must trigger AWAY from the current market in the closing direction:
      * SHORT SL is a stop-BUY → it must sit ABOVE market.
      * LONG  SL is a stop-SELL → it must sit BELOW market.

    Attributes:
        price: The stop price to submit — re-anchored to the correct side of
            market when the input was market-wrong-side; unchanged otherwise.
            Meaningless when ``should_flatten`` is True.
        was_reanchored: True when the input stop was on the wrong side of market
            (or inside the market-relative safety floor) and got moved out to
            ``market * (1 ± floor)`` on the correct side.
        should_flatten: True when no stop can be placed without immediate trigger
            — the caller must escalate to a reduce-only MARKET flatten instead of
            shipping a guaranteed -2021 price (or retrying it forever).
        reason: Short human-readable explanation; "" when no action was needed.
        original_price: The input stop price prior to any re-anchor.
    """

    price: float
    was_reanchored: bool
    should_flatten: bool
    reason: str
    original_price: float


def enforce_market_side_stop(
    *,
    position_side: PositionSide,
    stop_price: float,
    market_price: float,
    min_distance_pct: float,
    max_distance_pct: float = MAX_PLAUSIBLE_DISTANCE_PCT,
) -> MarketSideDecision:
    """Validate/re-anchor a stop-loss against the LIVE market price (#551).

    This is the market-relative gate that complements the entry-relative
    ``correct_protective_price``. It catches the market-crossed-entry case where
    an entry-side-correct stop would immediately trigger against the current
    market and leave the position naked.

    Invariant enforced (stop must trigger away from market in the close
    direction):
      * SHORT SL must be strictly ABOVE ``market * (1 + min_distance_pct)``.
      * LONG  SL must be strictly BELOW ``market * (1 - min_distance_pct)``.

    Args:
        position_side: "LONG" or "SHORT".
        stop_price: The (already entry-direction-corrected) stop price.
        market_price: Current mark/last price. MUST be > 0 — callers resolve it.
        min_distance_pct: Minimum |distance| from market (fraction, e.g. 0.06).
            A stop closer than this to market — or on the wrong side — is
            re-anchored out to the floor on the correct side.
        max_distance_pct: If the re-anchored floor stop would still exceed the
            widest placeable band (PERCENT_PRICE cap ~ this value), no stop can
            be placed; signal a flatten instead. Defaults to
            ``MAX_PLAUSIBLE_DISTANCE_PCT``. Since the floor is normally well
            inside this cap, ``should_flatten`` only fires when the caller has
            passed a floor >= the cap (pathological config) — the realistic
            flatten trigger is the caller acting on a subsequent PERCENT_PRICE
            rejection of the re-anchored price.

    Returns:
        MarketSideDecision describing the outcome.

    Raises:
        ValueError: If ``market_price`` is not strictly positive.
    """
    if market_price <= 0:
        raise ValueError(
            "market_price must be > 0 — callers must resolve a live market price"
        )

    # +1 => stop must be ABOVE market (SHORT), -1 => BELOW market (LONG).
    sign = +1 if position_side == "SHORT" else -1
    floor_edge = market_price * (1 + sign * min_distance_pct)

    # On the correct side AND at/beyond the market-relative floor → leave alone.
    beyond_floor = (sign > 0 and stop_price >= floor_edge) or (
        sign < 0 and stop_price <= floor_edge
    )
    if beyond_floor:
        return MarketSideDecision(
            price=stop_price,
            was_reanchored=False,
            should_flatten=False,
            reason="",
            original_price=stop_price,
        )

    # Wrong side of market, or inside the market-relative floor band: the stop
    # would immediately trigger (-2021). Re-anchor to the safety floor on the
    # correct side of the LIVE market.
    reanchored = floor_edge

    # If even the floor stop exceeds the widest placeable band, no stop can be
    # placed without immediate trigger — escalate to flatten (#551 AC3).
    if min_distance_pct >= max_distance_pct:
        reason = (
            f"{position_side} SL {stop_price:.6f} is wrong-side/inside floor of "
            f"live market {market_price:.6f}; required floor "
            f"{min_distance_pct * 100:.2f}% >= max placeable "
            f"{max_distance_pct * 100:.2f}% — cannot place any stop without "
            f"immediate trigger; FLATTEN required (#551)"
        )
        logger.error("⚠️ market-side stop unplaceable (#551): %s", reason)
        return MarketSideDecision(
            price=stop_price,
            was_reanchored=False,
            should_flatten=True,
            reason=reason,
            original_price=stop_price,
        )

    reason = (
        f"{position_side} SL {stop_price:.6f} would immediately trigger against "
        f"live market {market_price:.6f} (market crossed entry); RE-ANCHORED to "
        f"correct side of market at {reanchored:.6f} "
        f"({sign * min_distance_pct * 100:+.2f}% from market) (#551)"
    )
    logger.warning("⚠️ market-side SL re-anchor (#551): %s", reason)
    return MarketSideDecision(
        price=reanchored,
        was_reanchored=True,
        should_flatten=False,
        reason=reason,
        original_price=stop_price,
    )
