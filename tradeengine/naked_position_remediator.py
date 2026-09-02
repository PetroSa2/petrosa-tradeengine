"""
Exchange-authoritative naked-position remediator (#445).

Operates on the ``unhedged`` divergences emitted by
:class:`tradeengine.position_reconciler.PositionReconciler` and takes
**write** actions to re-arm protective stops or flatten the position
when re-arm fails within a bounded grace window.

This is the first remediator that does **not** key any decision off
the local position store — it iterates the Binance ``positionRisk``
snapshot directly. That property is the architectural fix for the
recurring fault cluster (#424 family): every prior remediation path
was either local-state-keyed or read-only, so positions orphaned
across Mongo blips (#442/#783) stayed naked.

Ships off-by-default. Operator flips the mode via
``TE_NAKED_POSITION_REMEDIATION_MODE`` after canary validation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from prometheus_client import Counter, Gauge, Histogram

from contracts.order import OrderStatus, TradeOrder

if TYPE_CHECKING:
    from tradeengine.exchange.binance import BinanceFuturesExchange
    from tradeengine.position_manager import PositionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics — #445 AC4
# ---------------------------------------------------------------------------

naked_position_detected_total = Counter(
    "tradeengine_naked_position_detected_total",
    "Unhedged positions observed by the exchange-authoritative remediator",
    ["symbol", "side"],
)

naked_position_rearmed_total = Counter(
    "tradeengine_naked_position_rearmed_total",
    "Naked positions re-armed (or attempted) by the remediator",
    ["symbol", "side", "outcome"],  # outcome: armed, armed_partial, failed
)

naked_position_flattened_total = Counter(
    "tradeengine_naked_position_flattened_total",
    "Naked positions flattened (reduce-only MARKET) after grace window",
    ["symbol", "side", "outcome"],  # outcome: flattened, failed, skipped
)

reconcile_lag_seconds = Histogram(
    "tradeengine_reconcile_lag_seconds",
    "Elapsed seconds between divergence first-seen and remediation action",
    ["action"],  # action: armed, flattened
)

# #547: malformed (inverted-sign) positions that can never be armed. Counted
# separately from naked_position_detected_total so operators can alert on the
# distinct "position side/sign mismatch" fault rather than lumping it in with
# ordinary unhedged positions.
malformed_position_total = Counter(
    "tradeengine_malformed_position_total",
    "Malformed hedge-mode positions (LONG with negative amt or SHORT with "
    "positive amt) observed by the remediator",
    ["symbol", "side"],
)

# #566: in arm_only mode a malformed position is never flattened by design —
# it alerts once (malformed_position_total) and then stays "skipped" every
# cycle indefinitely (pending operator action or a promotion to
# arm_or_flatten). That "stuck" state had no time-series visibility beyond
# the once-per-episode CRITICAL log, so operators could not tell a
# 2-minute-old malformed position from a 2-day-old one without grepping
# logs. This gauge tracks live age per (symbol, side) so it can be alerted
# on (e.g. "stuck > 1h") independent of remediation-mode promotion.
malformed_position_stuck_seconds = Gauge(
    "tradeengine_malformed_position_stuck_seconds",
    "Seconds a malformed (inverted-sign) position has been observed without "
    "being armed or flattened; 0 once resolved",
    ["symbol", "side"],
)

# #560: a position that keeps failing to re-arm every reconciliation cycle
# (e.g. a -4130 conflict that survives the cancel-and-retry in
# BinanceFuturesExchange._execute_with_retry) is backed off instead of being
# retried every ~interval_seconds forever. This counter fires once per
# escalation episode so operators can alert on a position stuck in backoff
# rather than discovering it only via the raw retry-storm log volume.
naked_position_arm_exhausted_total = Counter(
    "tradeengine_naked_position_arm_exhausted_total",
    "Positions backed off after repeated consecutive re-arm failures "
    "instead of being retried every reconciliation cycle",
    ["symbol", "side"],
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RemediationMode = Literal["off", "dry_run", "arm_only", "arm_or_flatten"]

# dispatcher.close_position_with_cleanup signature
CloseCallable = Callable[..., Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# NakedPositionRemediator
# ---------------------------------------------------------------------------


class NakedPositionRemediator:
    """Write-mode counterpart to :class:`PositionReconciler` (read-only).

    Inject as a dependency; the reconciler invokes
    :meth:`remediate` with the ``unhedged`` divergences list after each
    detection pass. The remediator decides, per mode, whether to re-arm
    or flatten.
    """

    def __init__(
        self,
        *,
        exchange: BinanceFuturesExchange,
        position_manager: PositionManager,
        close_position: CloseCallable,
        mode: RemediationMode = "off",
        flatten_grace_sec: int = 60,
        fallback_sl_pct: float = 2.0,
        fallback_tp_pct: float = 4.0,
        min_sl_distance_pct: float = 6.0,
        max_consecutive_arm_failures: int = 5,
        arm_backoff_cooldown_sec: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._exchange = exchange
        self._position_manager = position_manager
        self._close_position = close_position
        self._mode: RemediationMode = self._coerce_mode(mode)
        self._flatten_grace_sec = max(int(flatten_grace_sec), 0)
        self._fallback_sl_pct = float(fallback_sl_pct)
        self._fallback_tp_pct = float(fallback_tp_pct)
        # The exchange safety floor (te_min_sl_distance_pct). A stored
        # strategy SL tighter than this is un-armable — the price-adjuster
        # rejects it — so _derive_protective_prices widens it out to the
        # floor rather than handing the re-arm a guaranteed-to-fail price
        # (2026-07-20 second-wave OCO-orphan incident).
        self._min_sl_distance_pct = float(min_sl_distance_pct)
        # #560: cap consecutive re-arm failures per (symbol, side) before
        # backing off instead of retrying every reconciliation cycle
        # indefinitely (the "infinite retry loop" symptom).
        self._max_consecutive_arm_failures = max(int(max_consecutive_arm_failures), 1)
        self._arm_backoff_cooldown_sec = max(int(arm_backoff_cooldown_sec), 0)
        self._clock = clock
        # (symbol, side) -> first-seen monotonic timestamp
        self._first_seen: dict[tuple[str, str], float] = {}
        # #547: (symbol, side) keys already CRITICAL-logged as malformed, so
        # arm_only emits the alert once per detection episode rather than every
        # reconcile cycle. Cleared when the key is no longer diverging.
        self._malformed_alerted: set[tuple[str, str]] = set()
        # #560: (symbol, side) -> consecutive _rearm failure count, reset on
        # success or when the divergence clears.
        self._consecutive_arm_failures: dict[tuple[str, str], int] = {}
        # (symbol, side) -> monotonic timestamp until which re-arm attempts
        # are skipped (set once _max_consecutive_arm_failures is reached).
        self._arm_backoff_until: dict[tuple[str, str], float] = {}
        # (symbol, side) keys already CRITICAL-logged as arm-exhausted, so the
        # escalation alert fires once per backoff episode rather than every
        # reconcile cycle. Cleared when the key is no longer diverging or the
        # backoff window has expired and a fresh attempt is made.
        self._arm_exhausted_alerted: set[tuple[str, str]] = set()

    @staticmethod
    def _coerce_mode(mode: str) -> RemediationMode:
        normalized = (mode or "off").lower().strip()
        if normalized not in ("off", "dry_run", "arm_only", "arm_or_flatten"):
            logger.warning(
                "NakedPositionRemediator: unknown mode %r; falling back to 'off'",
                mode,
            )
            return "off"
        return normalized  # type: ignore[return-value]

    @property
    def mode(self) -> RemediationMode:
        return self._mode

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def remediate(
        self,
        unhedged_divergences: list[dict[str, Any]],
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Apply mode-appropriate remediation to the unhedged divergence list.

        Returns a small counts dict for tests and log surfaces:
        ``{detected, armed, flattened, skipped, failed}``.
        """
        counts = {"detected": 0, "armed": 0, "flattened": 0, "skipped": 0, "failed": 0}

        if not unhedged_divergences:
            # Clean pass — clear first-seen so future detections start fresh.
            self._first_seen.clear()
            # #547: also reset the malformed alert latch so a later
            # re-occurrence re-alerts CRITICAL.
            # #566: zero the stuck-duration gauge for any keys that were
            # malformed before this clean pass.
            for k in self._malformed_alerted:
                malformed_position_stuck_seconds.labels(symbol=k[0], side=k[1]).set(0)
            self._malformed_alerted.clear()
            # #560: also reset the arm-failure backoff state so a later
            # re-occurrence starts its own fresh failure count.
            self._consecutive_arm_failures.clear()
            self._arm_backoff_until.clear()
            self._arm_exhausted_alerted.clear()
            return counts

        now = self._clock()
        currently_unhedged: set[tuple[str, str]] = set()

        for div in unhedged_divergences:
            symbol = div["symbol"]
            side = div["side"]
            key = (symbol, side)
            currently_unhedged.add(key)
            counts["detected"] += 1
            naked_position_detected_total.labels(symbol=symbol, side=side).inc()

            first_seen_at = self._first_seen.setdefault(key, now)
            elapsed = now - first_seen_at

            # #547: a malformed (inverted-sign) position can never be armed —
            # a reduceOnly SL/TP against a wrong-signed side is direction-
            # invalid. Route it to a safe terminal action instead of looping.
            if div.get("category") == "malformed_position":
                outcome = await self._handle_malformed(div, elapsed, binance_positions)
                counts[outcome] += 1
                continue

            if self._mode == "off":
                counts["skipped"] += 1
                continue

            if self._mode == "dry_run":
                self._log_dry_run(div, elapsed)
                counts["skipped"] += 1
                continue

            # arm_only or arm_or_flatten
            should_flatten = (
                self._mode == "arm_or_flatten" and elapsed >= self._flatten_grace_sec
            )

            if should_flatten:
                ok = await self._flatten(div, binance_positions)
                if ok:
                    counts["flattened"] += 1
                    reconcile_lag_seconds.labels(action="flattened").observe(elapsed)
                    # Clear so re-detection doesn't immediately re-flatten.
                    self._first_seen.pop(key, None)
                else:
                    counts["failed"] += 1
            else:
                # #560: a position already backed off after repeated
                # consecutive re-arm failures is skipped (not retried) until
                # its cooldown expires, instead of hammering the same
                # doomed-to-fail placement every reconciliation cycle.
                backoff_until = self._arm_backoff_until.get(key)
                if backoff_until is not None and now < backoff_until:
                    counts["skipped"] += 1
                    continue

                ok = await self._rearm(div, binance_positions)
                if ok:
                    counts["armed"] += 1
                    reconcile_lag_seconds.labels(action="armed").observe(elapsed)
                    self._consecutive_arm_failures.pop(key, None)
                    self._arm_backoff_until.pop(key, None)
                    self._arm_exhausted_alerted.discard(key)
                else:
                    counts["failed"] += 1
                    fails = self._consecutive_arm_failures.get(key, 0) + 1
                    self._consecutive_arm_failures[key] = fails
                    if fails >= self._max_consecutive_arm_failures:
                        self._arm_backoff_until[key] = (
                            now + self._arm_backoff_cooldown_sec
                        )
                        if key not in self._arm_exhausted_alerted:
                            self._arm_exhausted_alerted.add(key)
                            naked_position_arm_exhausted_total.labels(
                                symbol=symbol, side=side
                            ).inc()
                            logger.critical(
                                "NakedPositionRemediator: %s/%s failed to "
                                "re-arm %d consecutive times — backing off "
                                "for %ds instead of retrying every cycle. "
                                "Position may still be naked; operator "
                                "action required (#560).",
                                symbol,
                                side,
                                fails,
                                self._arm_backoff_cooldown_sec,
                            )

        # Drop first-seen entries for keys no longer unhedged (re-arm
        # succeeded between cycles).
        stale = [k for k in self._first_seen if k not in currently_unhedged]
        for k in stale:
            self._first_seen.pop(k, None)
        # #547: reset the once-per-episode malformed alert latch for any key
        # that resolved, so a future re-occurrence re-alerts CRITICAL again.
        # #566: also zero the stuck-duration gauge for the same resolved keys.
        for k in list(self._malformed_alerted):
            if k not in currently_unhedged:
                self._malformed_alerted.discard(k)
                malformed_position_stuck_seconds.labels(symbol=k[0], side=k[1]).set(0)
        # #560: reset the arm-failure backoff state for any key that
        # resolved, so a future re-occurrence starts a fresh failure count.
        for k in list(self._consecutive_arm_failures):
            if k not in currently_unhedged:
                self._consecutive_arm_failures.pop(k, None)
        for k in list(self._arm_backoff_until):
            if k not in currently_unhedged:
                self._arm_backoff_until.pop(k, None)
        for k in list(self._arm_exhausted_alerted):
            if k not in currently_unhedged:
                self._arm_exhausted_alerted.discard(k)

        return counts

    # ------------------------------------------------------------------
    # Mode handlers
    # ------------------------------------------------------------------

    def _log_dry_run(self, div: dict[str, Any], elapsed: float) -> None:
        symbol = div["symbol"]
        side = div["side"]
        missing = []
        if not div.get("sl_present"):
            missing.append("SL")
        if not div.get("tp_present"):
            missing.append("TP")
        would_flatten = (
            self._mode == "arm_or_flatten" and elapsed >= self._flatten_grace_sec
        )
        action = "flatten" if would_flatten else "arm"
        logger.warning(
            "NakedPositionRemediator[dry_run]: would %s %s/%s qty=%s missing=%s "
            "first_seen_age=%.1fs",
            action,
            symbol,
            side,
            div.get("binance_qty"),
            "+".join(missing) or "none",
            elapsed,
        )

    async def _rearm(
        self,
        div: dict[str, Any],
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None,
    ) -> bool:
        """Attempt to place the missing reduceOnly SL and/or TP.

        Re-arm uses the stored strategy SL/TP prices when present in the
        local position store, otherwise falls back to a configurable
        % distance from the exchange-reported ``entryPrice``. Returns
        True when at least one missing leg was placed without error.
        """
        symbol = div["symbol"]
        side = div["side"]
        qty = float(div.get("binance_qty") or 0.0)
        if qty <= 0:
            naked_position_rearmed_total.labels(
                symbol=symbol, side=side, outcome="failed"
            ).inc()
            return False

        sl_price, tp_price, must_flatten = self._derive_protective_prices(
            symbol, side, binance_positions
        )

        # #551: no placeable stop exists (market crossed entry such that any SL
        # would immediately trigger). Retrying the same dead price forever is
        # exactly the observed naked-forever loop — escalate to a reduce-only
        # MARKET flatten instead, regardless of the grace window, when the mode
        # permits flattening. In arm_only mode we cannot flatten, so record a
        # failure so operators see the position is stuck.
        if must_flatten:
            if self._mode == "arm_or_flatten":
                logger.error(
                    "NakedPositionRemediator: %s/%s has no placeable SL vs live "
                    "market — escalating to flatten instead of re-arming a "
                    "guaranteed -2021 price (#551)",
                    symbol,
                    side,
                )
                return await self._flatten(div, binance_positions)
            logger.error(
                "NakedPositionRemediator: %s/%s has no placeable SL vs live "
                "market but mode=%s cannot flatten — leaving for grace-window "
                "flatten; NOT re-arming a guaranteed -2021 price (#551)",
                symbol,
                side,
                self._mode,
            )
            naked_position_rearmed_total.labels(
                symbol=symbol, side=side, outcome="failed"
            ).inc()
            return False

        sl_missing = not div.get("sl_present")
        tp_missing = not div.get("tp_present")
        order_side = "sell" if side == "LONG" else "buy"
        placed_any = False
        had_failure = False

        if sl_missing and sl_price is not None:
            try:
                await self._exchange.execute(
                    TradeOrder(
                        symbol=symbol,
                        side=order_side,  # type: ignore[arg-type]
                        type="stop",  # type: ignore[arg-type]
                        amount=qty,
                        stop_loss=float(sl_price),
                        position_side=side,
                        reduce_only=True,
                        status=OrderStatus.PENDING,
                    )
                )
                placed_any = True
                logger.info(
                    "NakedPositionRemediator: re-armed SL on %s/%s qty=%s price=%s",
                    symbol,
                    side,
                    qty,
                    sl_price,
                )
            except Exception:
                had_failure = True
                logger.exception(
                    "NakedPositionRemediator: SL re-arm failed for %s/%s",
                    symbol,
                    side,
                )

        if tp_missing and tp_price is not None:
            try:
                await self._exchange.execute(
                    TradeOrder(
                        symbol=symbol,
                        side=order_side,  # type: ignore[arg-type]
                        type="take_profit",  # type: ignore[arg-type]
                        amount=qty,
                        take_profit=float(tp_price),
                        position_side=side,
                        reduce_only=True,
                        status=OrderStatus.PENDING,
                    )
                )
                placed_any = True
                logger.info(
                    "NakedPositionRemediator: re-armed TP on %s/%s qty=%s price=%s",
                    symbol,
                    side,
                    qty,
                    tp_price,
                )
            except Exception:
                had_failure = True
                logger.exception(
                    "NakedPositionRemediator: TP re-arm failed for %s/%s",
                    symbol,
                    side,
                )

        outcome = (
            "armed"
            if placed_any and not had_failure
            else ("armed_partial" if placed_any else "failed")
        )
        naked_position_rearmed_total.labels(
            symbol=symbol, side=side, outcome=outcome
        ).inc()
        return placed_any

    async def _flatten(
        self,
        div: dict[str, Any],
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None,
        reason: str = "naked_position_grace_expired",
    ) -> bool:
        """Reduce-only MARKET close via dispatcher.close_position_with_cleanup.

        ``reason`` defaults to the naked-grace-expired label; #547 passes
        ``"malformed_position"`` so the audit trail distinguishes a flatten
        triggered by an inverted-sign position from an ordinary grace flatten.
        """
        symbol = div["symbol"]
        side = div["side"]
        qty = float(div.get("binance_qty") or 0.0)
        if qty <= 0:
            naked_position_flattened_total.labels(
                symbol=symbol, side=side, outcome="skipped"
            ).inc()
            return False

        position_id = self._resolve_position_id(symbol, side)
        try:
            result = await self._close_position(
                position_id=position_id,
                symbol=symbol,
                position_side=side,
                quantity=qty,
                reason=reason,
            )
        except Exception:
            logger.exception(
                "NakedPositionRemediator: flatten failed for %s/%s qty=%s",
                symbol,
                side,
                qty,
            )
            naked_position_flattened_total.labels(
                symbol=symbol, side=side, outcome="failed"
            ).inc()
            return False

        ok = bool(result) and not (
            isinstance(result, dict)
            and result.get("status") in ("failed", "rejected", "error")
        )
        logger.warning(
            "NakedPositionRemediator: flattened %s/%s qty=%s reason=%s result=%s",
            symbol,
            side,
            qty,
            reason,
            result,
        )
        naked_position_flattened_total.labels(
            symbol=symbol, side=side, outcome="flattened" if ok else "failed"
        ).inc()
        return ok

    async def _handle_malformed(
        self,
        div: dict[str, Any],
        elapsed: float,
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None,
    ) -> str:
        """#547: safe terminal handling for an inverted-sign position.

        A malformed position (LONG with negative ``positionAmt`` or SHORT with
        positive) is un-armable: any ``reduceOnly`` protective order derived
        from the declared side is direction-invalid. Rather than loop forever:

        - ``off`` / ``dry_run``: observe only (``skipped``).
        - ``arm_only``: increment ``tradeengine_malformed_position_total``, log
          CRITICAL once per detection episode, and take NO arm action (a
          guaranteed-to-fail arm every cycle is exactly the bug). Returns
          ``skipped`` — the position is stuck pending human/mode intervention.
        - ``arm_or_flatten``: after ``flatten_grace_sec``, flatten reduce-only
          MARKET with ``reason="malformed_position"``; before grace, alert like
          arm_only. Returns ``flattened`` / ``failed`` / ``skipped``.
        """
        symbol = div["symbol"]
        side = div["side"]
        key = (symbol, side)

        if self._mode in ("off", "dry_run"):
            if self._mode == "dry_run":
                logger.warning(
                    "NakedPositionRemediator[dry_run]: would remediate MALFORMED "
                    "%s/%s raw_amt=%s (sign/side mismatch) first_seen_age=%.1fs",
                    symbol,
                    side,
                    div.get("raw_position_amt"),
                    elapsed,
                )
            return "skipped"

        # arm_only OR arm_or_flatten before grace → alert, never arm.
        malformed_position_total.labels(symbol=symbol, side=side).inc()
        malformed_position_stuck_seconds.labels(symbol=symbol, side=side).set(elapsed)
        if key not in self._malformed_alerted:
            self._malformed_alerted.add(key)
            logger.critical(
                "NakedPositionRemediator: MALFORMED position %s/%s "
                "raw_positionAmt=%s (positionSide=%s sign mismatch) — cannot be "
                "armed with a direction-valid reduceOnly SL/TP. mode=%s. %s (#547)",
                symbol,
                side,
                div.get("raw_position_amt"),
                side,
                self._mode,
                (
                    "Will flatten after grace window."
                    if self._mode == "arm_or_flatten"
                    else "arm_only cannot flatten — position is stuck pending "
                    "operator action or arm_or_flatten mode."
                ),
            )

        should_flatten = (
            self._mode == "arm_or_flatten" and elapsed >= self._flatten_grace_sec
        )
        if not should_flatten:
            # arm_only always, and arm_or_flatten pre-grace: no arm attempt.
            return "skipped"

        ok = await self._flatten(div, binance_positions, reason="malformed_position")
        if ok:
            reconcile_lag_seconds.labels(action="flattened").observe(elapsed)
            self._first_seen.pop(key, None)
            self._malformed_alerted.discard(key)
            malformed_position_stuck_seconds.labels(symbol=symbol, side=side).set(0)
            return "flattened"
        return "failed"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _derive_protective_prices(
        self,
        symbol: str,
        side: str,
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None,
    ) -> tuple[float | None, float | None, bool]:
        """Return ``(sl_price, tp_price, must_flatten)`` using local-strategy
        values when present, else fall back to ``entryPrice ± fallback_pct``.

        Local strategy values are preferred so re-arm matches strategy
        intent. Fallback exists because the whole point of #445 is that
        local state may be stale or missing — the exchange position is
        the ground truth.

        Second-wave OCO-orphan fix (2026-07-20): a stored strategy SL that
        is tighter than the exchange safety floor (te_min_sl_distance_pct)
        is un-armable — the price-adjuster rejects it and the re-arm fails,
        degrading arm_or_flatten to flatten-everything. When we know the
        entry price, any SL inside the floor band is WIDENED out to the
        floor so the re-arm can actually place. TP is not floor-constrained.

        Market-crossed-entry fix (#551): the entry-anchored SL floor above
        only guarantees the stop is correct-side of ENTRY. If the market has
        crossed entry (position underwater), an entry-side-correct SL can sit
        on the wrong side of the LIVE ``markPrice`` and immediately trigger
        (-2021) — the re-arm then retries the identical dead price forever.
        We re-anchor the SL to the correct side of ``markPrice``; when no stop
        is placeable without immediate trigger the third return value
        ``must_flatten`` is True so the caller escalates instead of looping.
        """
        sl_price: float | None = None
        tp_price: float | None = None
        must_flatten = False

        try:
            local = self._position_manager.get_positions().get((symbol, side))
        except Exception:
            local = None

        if local:
            try:
                lsl = local.get("stop_loss_price")
                if lsl is not None:
                    sl_price = float(lsl)
            except (TypeError, ValueError):
                pass
            try:
                ltp = local.get("take_profit_price")
                if ltp is not None:
                    tp_price = float(ltp)
            except (TypeError, ValueError):
                pass

        # Resolve entry price unconditionally — needed both for the fallback
        # legs AND to clamp a too-tight stored SL out to the safety floor.
        entry_price: float | None = None
        if binance_positions:
            bp = binance_positions.get((symbol, side))
            if bp:
                try:
                    entry_price = float(bp.get("entryPrice") or 0.0) or None
                except (TypeError, ValueError):
                    entry_price = None

        # Without an entry price we cannot compute the floor band or the
        # fallback — return whatever local values we have (legacy behaviour).
        if entry_price is None or entry_price <= 0:
            return sl_price, tp_price, must_flatten

        # Fill missing legs from the entry-anchored fallback.
        if sl_price is None:
            if side == "LONG":
                sl_price = entry_price * (1.0 - self._fallback_sl_pct / 100.0)
            else:
                sl_price = entry_price * (1.0 + self._fallback_sl_pct / 100.0)
        if tp_price is None:
            if side == "LONG":
                tp_price = entry_price * (1.0 + self._fallback_tp_pct / 100.0)
            else:
                tp_price = entry_price * (1.0 - self._fallback_tp_pct / 100.0)

        # Clamp a too-tight SL (stored OR fallback) out to the safety floor.
        # LONG SL is below entry: it must be <= entry * (1 - floor).
        # SHORT SL is above entry: it must be >= entry * (1 + floor).
        if sl_price is not None and self._min_sl_distance_pct > 0:
            floor = self._min_sl_distance_pct / 100.0
            if side == "LONG":
                floor_price = entry_price * (1.0 - floor)
                if sl_price > floor_price:
                    logger.warning(
                        "NakedPositionRemediator: widening too-tight LONG SL "
                        "%s/%s from %s to floor %s (entry=%s, floor=%.2f%%)",
                        symbol,
                        side,
                        sl_price,
                        floor_price,
                        entry_price,
                        self._min_sl_distance_pct,
                    )
                    sl_price = floor_price
            else:
                floor_price = entry_price * (1.0 + floor)
                if sl_price < floor_price:
                    logger.warning(
                        "NakedPositionRemediator: widening too-tight SHORT SL "
                        "%s/%s from %s to floor %s (entry=%s, floor=%.2f%%)",
                        symbol,
                        side,
                        sl_price,
                        floor_price,
                        entry_price,
                        self._min_sl_distance_pct,
                    )
                    sl_price = floor_price

        # Market-crossed-entry gate (#551): the entry-anchored floor above does
        # not know where the LIVE market is. If the market has crossed entry,
        # an entry-side-correct SL can be wrong-side of markPrice and trigger
        # immediately (-2021). Re-anchor to the correct side of markPrice; if no
        # placeable stop exists, signal a flatten so the caller stops retrying
        # the identical dead price.
        mark_price: float | None = None
        if binance_positions:
            bp = binance_positions.get((symbol, side))
            if bp:
                try:
                    mark_price = float(bp.get("markPrice") or 0.0) or None
                except (TypeError, ValueError):
                    mark_price = None

        if sl_price is not None and mark_price and mark_price > 0:
            from tradeengine.risk.sl_tp_direction import enforce_market_side_stop

            decision = enforce_market_side_stop(
                position_side=side,  # type: ignore[arg-type]
                stop_price=float(sl_price),
                market_price=mark_price,
                min_distance_pct=self._min_sl_distance_pct / 100.0,
            )
            if decision.should_flatten:
                logger.error(
                    "NakedPositionRemediator: %s/%s SL unplaceable vs live "
                    "market %s — %s; signalling flatten (#551)",
                    symbol,
                    side,
                    mark_price,
                    decision.reason,
                )
                must_flatten = True
            elif decision.was_reanchored:
                logger.warning(
                    "NakedPositionRemediator: re-anchoring %s/%s SL from %s to "
                    "%s (correct side of live market %s) (#551)",
                    symbol,
                    side,
                    sl_price,
                    decision.price,
                    mark_price,
                )
                sl_price = decision.price

        return sl_price, tp_price, must_flatten

    def _resolve_position_id(self, symbol: str, side: str) -> str:
        """Best-effort: use the local position's id if known, otherwise
        synthesize a stable handle the dispatcher accepts."""
        try:
            local = self._position_manager.get_positions().get((symbol, side))
        except Exception:
            local = None
        if local:
            pid = local.get("position_id") or local.get("strategy_position_id")
            if pid:
                return str(pid)
        return f"naked-{symbol}-{side}"
