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

from prometheus_client import Counter, Histogram

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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._exchange = exchange
        self._position_manager = position_manager
        self._close_position = close_position
        self._mode: RemediationMode = self._coerce_mode(mode)
        self._flatten_grace_sec = max(int(flatten_grace_sec), 0)
        self._fallback_sl_pct = float(fallback_sl_pct)
        self._fallback_tp_pct = float(fallback_tp_pct)
        self._clock = clock
        # (symbol, side) -> first-seen monotonic timestamp
        self._first_seen: dict[tuple[str, str], float] = {}

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
                ok = await self._rearm(div, binance_positions)
                if ok:
                    counts["armed"] += 1
                    reconcile_lag_seconds.labels(action="armed").observe(elapsed)
                else:
                    counts["failed"] += 1

        # Drop first-seen entries for keys no longer unhedged (re-arm
        # succeeded between cycles).
        stale = [k for k in self._first_seen if k not in currently_unhedged]
        for k in stale:
            self._first_seen.pop(k, None)

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

        sl_price, tp_price = self._derive_protective_prices(
            symbol, side, binance_positions
        )

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
    ) -> bool:
        """Reduce-only MARKET close via dispatcher.close_position_with_cleanup."""
        symbol = div["symbol"]
        side = div["side"]
        qty = float(div.get("binance_qty") or 0.0)
        if qty <= 0:
            naked_position_flattened_total.labels(
                symbol=symbol, side=side, outcome="skipped"
            ).inc()
            return False

        position_id = self._resolve_position_id(symbol, side)
        reason = "naked_position_grace_expired"
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _derive_protective_prices(
        self,
        symbol: str,
        side: str,
        binance_positions: dict[tuple[str, str], dict[str, Any]] | None,
    ) -> tuple[float | None, float | None]:
        """Return (sl_price, tp_price) using local-strategy values when
        present, else fall back to ``entryPrice ± fallback_pct``.

        Local strategy values are preferred so re-arm matches strategy
        intent. Fallback exists because the whole point of #445 is that
        local state may be stale or missing — the exchange position is
        the ground truth.
        """
        sl_price: float | None = None
        tp_price: float | None = None

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

        if sl_price is not None and tp_price is not None:
            return sl_price, tp_price

        entry_price: float | None = None
        if binance_positions:
            bp = binance_positions.get((symbol, side))
            if bp:
                try:
                    entry_price = float(bp.get("entryPrice") or 0.0) or None
                except (TypeError, ValueError):
                    entry_price = None

        if entry_price is None or entry_price <= 0:
            return sl_price, tp_price

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
        return sl_price, tp_price

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
