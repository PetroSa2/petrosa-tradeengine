"""
Position-state reconciliation (FR65, AC1–AC5, AC8).

Compares TradeEngine's local position tracker against Binance's live
positionRisk snapshot on a configurable cadence.  Divergences emit an
unhealthy execution-evaluator metric (AC3/FR21) and a structured alert
(AC4/FR66 category e).  Read-only — never modifies local or exchange
state (AC5).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from prometheus_client import Counter, Gauge

from tradeengine.exchange_truth_store import (
    ExchangeTruthStore,
    exchange_truth_store_stale_seconds,
)

if TYPE_CHECKING:
    from tradeengine.exchange.binance import BinanceFuturesExchange
    from tradeengine.naked_position_remediator import NakedPositionRemediator
    from tradeengine.position_manager import PositionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

reconciliation_runs_total = Counter(
    "tradeengine_position_reconciliation_runs_total",
    "Total reconciliation runs",
    ["result"],  # "ok" | "error"
)

reconciliation_divergences_total = Counter(
    "tradeengine_position_reconciliation_divergences_total",
    "Position divergences detected by category",
    ["category", "symbol"],
)

# AC3 / FR21: execution-evaluator verdict (0 = healthy, 1 = unhealthy)
reconciliation_evaluator_verdict = Gauge(
    "tradeengine_position_reconciliation_evaluator_verdict",
    "Execution-evaluator verdict: 0=healthy, 1=unhealthy (FR65/FR21)",
)

# AC4 / FR66 category e: alert fires when divergences are active
reconciliation_alert = Gauge(
    "tradeengine_position_reconciliation_alert",
    "1 when position divergences are present, 0 when clean (FR66 category e)",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Treat |Δqty| below this as rounding noise rather than a real mismatch
_FLOAT_TOLERANCE = 1e-4


# ---------------------------------------------------------------------------
# Pure helpers (easy to unit-test)
# ---------------------------------------------------------------------------


def _normalise_side(pos: dict[str, Any]) -> str:
    """Return 'LONG' or 'SHORT' from a Binance positionRisk record."""
    side = str(pos.get("positionSide", "BOTH")).upper()
    if side in ("LONG", "SHORT"):
        return side
    # ONE-WAY mode: derive from sign of positionAmt
    return "LONG" if float(pos.get("positionAmt", 0)) >= 0 else "SHORT"


def _index_binance_positions(
    raw: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Filter raw positionRisk list to non-zero positions.

    Returns a dict keyed by (symbol, normalised_side).
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for pos in raw:
        if abs(float(pos.get("positionAmt", 0))) < _FLOAT_TOLERANCE:
            continue
        symbol: str = pos["symbol"]
        side = _normalise_side(pos)
        out[(symbol, side)] = pos
    return out


def detect_divergences(
    binance_positions: dict[tuple[str, str], dict[str, Any]],
    local_positions: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """AC2: return structured divergence records.

    Three categories (AC5 of #424 adds a fourth — see
    :func:`detect_unhedged_positions`):
    - untracked: Binance has a non-zero position, local tracker is empty
    - ghost:     local tracker has a position, Binance shows nothing
    - mutation:  both exist but quantity differs beyond tolerance
    """
    divergences: list[dict[str, Any]] = []

    # untracked
    for (symbol, side), bp in binance_positions.items():
        if (symbol, side) not in local_positions:
            divergences.append(
                {
                    "category": "untracked",
                    "symbol": symbol,
                    "side": side,
                    "binance_qty": abs(float(bp.get("positionAmt", 0))),
                    "local_qty": 0.0,
                    "detail": "Position on Binance but absent from local tracker",
                }
            )

    # ghost + mutation
    for (symbol, side), lp in local_positions.items():
        local_qty = abs(float(lp.get("quantity", lp.get("amount", 0))))
        if (symbol, side) not in binance_positions:
            divergences.append(
                {
                    "category": "ghost",
                    "symbol": symbol,
                    "side": side,
                    "binance_qty": 0.0,
                    "local_qty": local_qty,
                    "detail": "Position in local tracker but absent from Binance",
                }
            )
        else:
            binance_qty = abs(
                float(binance_positions[(symbol, side)].get("positionAmt", 0))
            )
            if abs(binance_qty - local_qty) > _FLOAT_TOLERANCE:
                divergences.append(
                    {
                        "category": "mutation",
                        "symbol": symbol,
                        "side": side,
                        "binance_qty": binance_qty,
                        "local_qty": local_qty,
                        "detail": (
                            f"Size mismatch: Binance={binance_qty:.6f}, local={local_qty:.6f}"
                        ),
                    }
                )

    return divergences


def _order_is_reduce_only(order: dict[str, Any]) -> bool:
    """Treat both ``reduceOnly=True`` and ``closePosition=True`` as
    reduce-only — Binance uses ``closePosition`` for sweep-everything
    SL/TP and ``reduceOnly`` for sized stops; either flag protects the
    position from further accumulation."""
    return bool(order.get("reduceOnly")) or bool(order.get("closePosition"))


def detect_unhedged_positions(
    binance_positions: dict[tuple[str, str], dict[str, Any]],
    binance_open_orders_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """AC5 of #424: detect positions on Binance with no matching SL+TP.

    For each non-zero Binance position, scan the open-order list for the
    same ``(symbol, positionSide)`` and require at least one reduceOnly
    STOP-shaped order AND at least one reduceOnly TAKE_PROFIT-shaped
    order. Anything less is unhedged — emit a structured divergence.

    The 2026-05-30 incident had 11/12 live positions unhedged on Binance
    while the reconciler reported clean — root cause #5 of #424.
    """
    divergences: list[dict[str, Any]] = []

    for (symbol, side), bp in binance_positions.items():
        orders = binance_open_orders_by_symbol.get(symbol, []) or []
        sl_present = False
        tp_present = False
        for o in orders:
            o_side = str(o.get("positionSide", "BOTH")).upper()
            # Hedge-mode rows must match exactly; one-way-mode rows ("BOTH")
            # cover any side.
            if o_side not in ("BOTH", side):
                continue
            if not _order_is_reduce_only(o):
                continue
            o_type = str(o.get("type") or o.get("origType") or "").upper()
            if "STOP" in o_type:
                sl_present = True
            elif "TAKE_PROFIT" in o_type:
                tp_present = True

        if sl_present and tp_present:
            continue

        # Build a precise human-readable detail for the alert payload.
        missing: list[str] = []
        if not sl_present:
            missing.append("SL")
        if not tp_present:
            missing.append("TP")
        divergences.append(
            {
                "category": "unhedged",
                "symbol": symbol,
                "side": side,
                "binance_qty": abs(float(bp.get("positionAmt", 0))),
                "local_qty": 0.0,
                "sl_present": sl_present,
                "tp_present": tp_present,
                "detail": (
                    f"Position on Binance lacks reduceOnly {'+'.join(missing)} "
                    f"order(s) — unhedged"
                ),
            }
        )

    return divergences


# ---------------------------------------------------------------------------
# PositionReconciler
# ---------------------------------------------------------------------------


class PositionReconciler:
    """FR65: periodic read-only reconciliation of local vs Binance positions.

    Start via ``await reconciler.start()``; stop via ``await reconciler.stop()``.
    Call ``reconcile_once()`` directly in tests.
    """

    def __init__(
        self,
        exchange: BinanceFuturesExchange,
        position_manager: PositionManager,
        interval_seconds: int = 60,
        remediator: NakedPositionRemediator | None = None,
        store: ExchangeTruthStore | None = None,
    ) -> None:
        self._exchange = exchange
        self._position_manager = position_manager
        self._interval = interval_seconds
        self._remediator = remediator
        self._store = store
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._last_divergence_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """AC1: launch the periodic reconciliation loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="position-reconciler")
        logger.info("PositionReconciler started (interval=%ss)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PositionReconciler stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PositionReconciler._loop unhandled error")
                reconciliation_runs_total.labels(result="error").inc()
            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------
    # Core reconciliation
    # ------------------------------------------------------------------

    async def reconcile_once(self) -> list[dict[str, Any]]:
        """Run one reconciliation pass; return the divergence list."""
        try:
            raw = await self._exchange.get_position_info()
        except Exception:
            logger.exception(
                "PositionReconciler: failed to fetch Binance position info"
            )
            reconciliation_runs_total.labels(result="error").inc()
            return []

        binance_positions = _index_binance_positions(raw)
        local_positions = self._position_manager.get_positions()

        divergences = detect_divergences(binance_positions, local_positions)

        # AC5 of #424: also detect positions on Binance with no matching
        # reduceOnly SL+TP orders. Fetch open algo orders per unique
        # symbol present in binance_positions and append unhedged
        # divergences to the same list so the existing metric/alert
        # paths surface them uniformly.
        unhedged, orders_by_symbol = await self._detect_unhedged_for(binance_positions)
        divergences.extend(unhedged)

        self._last_divergence_count = len(divergences)

        for d in divergences:
            reconciliation_divergences_total.labels(
                category=d["category"], symbol=d["symbol"]
            ).inc()

        if divergences:
            self._emit_unhealthy(divergences)
        else:
            reconciliation_evaluator_verdict.set(0)
            reconciliation_alert.set(0)
            logger.debug("PositionReconciler: positions clean, no divergences")

        # AC1 (446-B) — write REST snapshot into ExchangeTruthStore so the store
        # stays accurate even when the stream missed events or was briefly down.
        if self._store is not None:
            all_orders = [o for orders in orders_by_symbol.values() for o in orders]
            try:
                await self._store.update_from_rest(raw, all_orders)
            except Exception:
                logger.exception(
                    "PositionReconciler: store.update_from_rest raised — continuing"
                )
            # AC2 (446-B) — log stale-stream warning metric
            stream_ts = self._store.last_updated
            if stream_ts is not None:
                stale_secs = (datetime.now(UTC) - stream_ts).total_seconds()
                exchange_truth_store_stale_seconds.set(stale_secs)
                if stale_secs > 2 * self._interval:
                    logger.warning(
                        "ExchangeTruthStore stream stale: %.0fs (threshold=%ds)",
                        stale_secs,
                        2 * self._interval,
                    )

        # #445: hand the unhedged subset to the write-mode remediator.
        # When mode == "off" (default), this is a no-op. The remediator
        # owns its own metrics/logging; failures here must not poison
        # the read-only reconciliation pass.
        if self._remediator is not None:
            unhedged_only = [d for d in divergences if d.get("category") == "unhedged"]
            try:
                await self._remediator.remediate(
                    unhedged_only, binance_positions=binance_positions
                )
            except Exception:
                logger.exception(
                    "PositionReconciler: remediator raised — read-only "
                    "reconciliation pass continues"
                )

        reconciliation_runs_total.labels(result="ok").inc()
        return divergences

    async def _detect_unhedged_for(
        self,
        binance_positions: dict[tuple[str, str], dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """AC5 helper: fetch open algo orders for each unique symbol and
        delegate to :func:`detect_unhedged_positions`. Returns (divergences,
        orders_by_symbol) so the caller can feed orders into the store.
        Never raises into the caller — reconciliation should keep running."""
        if not binance_positions:
            return [], {}
        symbols = {symbol for symbol, _ in binance_positions.keys()}
        orders_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            try:
                orders = await self._exchange.get_open_algo_orders(symbol=symbol)
            except Exception:
                logger.exception(
                    "PositionReconciler: get_open_algo_orders(%s) failed; "
                    "skipping unhedged check for this symbol",
                    symbol,
                )
                orders = []
            orders_by_symbol[symbol] = orders or []
        return detect_unhedged_positions(
            binance_positions, orders_by_symbol
        ), orders_by_symbol

    # ------------------------------------------------------------------
    # Alert / evaluator helpers
    # ------------------------------------------------------------------

    def _emit_unhealthy(self, divergences: list[dict[str, Any]]) -> None:
        """AC3 + AC4: set unhealthy verdict and FR66 category-e alert."""
        summary = "; ".join(
            f"{d['category']}:{d['symbol']}:{d['side']}" for d in divergences
        )
        reconciliation_evaluator_verdict.set(1)
        reconciliation_alert.set(1)
        logger.warning(
            "PositionReconciler: %d divergence(s) — evaluator.execution.verdict=unhealthy. %s",
            len(divergences),
            summary,
        )

    # ------------------------------------------------------------------
    # Health check (duck-typed for dispatcher.health_check)
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        divergence_count = self._last_divergence_count
        return {
            "status": "unhealthy" if divergence_count > 0 else "healthy",
            "divergence_count": divergence_count,
            "interval_seconds": self._interval,
        }
