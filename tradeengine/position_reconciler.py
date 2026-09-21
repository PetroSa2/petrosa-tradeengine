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

from shared.constants import HEDGE_MODE_ENABLED
from tradeengine.exchange_truth_store import (
    ExchangeTruthStore,
    exchange_truth_store_stale_seconds,
)
from tradeengine.ghost_position_remediator import GhostPositionRemediator

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

# #566: the reconciler classifies positionSide LONG/SHORT rows assuming the
# account is in Binance hedge mode (dualSidePosition=true). If the account's
# actual setting ever drifts from that assumption, `_is_malformed_sign` and
# `_normalise_side` are interpreting the wrong contract. This gauge makes that
# assumption independently verifiable rather than implicitly trusted.
hedge_mode_mismatch = Gauge(
    "tradeengine_hedge_mode_mismatch",
    "1 when the account's actual dualSidePosition setting differs from the "
    "hedge-mode assumption the reconciler uses to classify positions, "
    "0 when confirmed matching or verification is inconclusive",
)

# #587: independent count-level check. detect_divergences() already compares
# per-(symbol,side) rows between `binance_positions` and
# `position_manager.get_positions()` — but when TE_EXCHANGE_TRUTH_STORE_ENABLED
# is "on", get_positions() itself already returns exchange-sourced snapshots,
# so that comparison can read "clean" even while the raw local audit journal
# (`position_manager.positions`, which feeds any code path that still reads
# it directly instead of through the accessor) has drifted arbitrarily far
# from exchange truth — exactly the class of bug behind #587 (13 vs 1).
raw_journal_count_mismatch = Gauge(
    "tradeengine_raw_journal_count_mismatch",
    "1 when len(position_manager.positions) (raw local audit journal) "
    "differs from what position_manager.get_positions() reports (the "
    "exchange-authoritative accessor when TE_EXCHANGE_TRUTH_STORE_ENABLED=on), "
    "0 when they agree (#587)",
)

# #592: ternary verdict state, in addition to the binary
# `reconciliation_evaluator_verdict` gauge above (kept for backwards
# compatibility with existing alert rules keyed on ==1). A single
# unresolved `ghost` or `raw_journal_count_mismatch` divergence is
# self-healing (see GhostPositionRemediator + the ExchangeTruthStore REST
# refresh) and must not hard-block intake the way untracked/mutation/
# unhedged/malformed_position divergences do.
reconciliation_verdict_state = Gauge(
    "tradeengine_position_reconciliation_verdict_state",
    "Execution-evaluator verdict: 0=healthy, 1=degraded, 2=unhealthy (#592)",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Treat |Δqty| below this as rounding noise rather than a real mismatch
_FLOAT_TOLERANCE = 1e-4

# #592: divergence categories that are journal-only/local-bookkeeping issues
# rather than evidence of unsafe exchange state. A cycle whose divergences
# are made up ENTIRELY of these categories degrades the verdict instead of
# hard-failing it — see `classify_verdict`.
_DEGRADED_CATEGORIES = frozenset({"ghost", "raw_journal_count_mismatch"})

_VERDICT_STATE_VALUES = {"healthy": 0, "degraded": 1, "unhealthy": 2}

# #609: minimum gap between forced-reconnect attempts triggered by detected
# stream staleness. reconcile_once() runs every `interval_seconds` (default
# 60s) and the staleness condition (`stale_secs > 2 * interval`) stays true
# on every cycle until a reconnect actually lands a fresh event, so without
# this cooldown a persistently-stale-but-still-"connected" stream would be
# force-reconnected once per reconcile cycle. Bounding it to one attempt per
# cooldown window still self-heals well within #609 AC5's 24h window while
# avoiding a reconnect storm.
_STREAM_RECONNECT_COOLDOWN_SECS = 300


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


def _is_malformed_sign(side: str, position_amt: float) -> bool:
    """#547: detect an inverted-sign hedge-mode position.

    In hedge mode a ``LONG`` leg must carry a positive ``positionAmt`` and a
    ``SHORT`` leg a negative one. A ``LONG`` row with ``positionAmt < 0`` (or a
    ``SHORT`` row with ``positionAmt > 0``) is internally inconsistent: any
    ``reduceOnly`` protective order derived from the declared side is
    direction-invalid, so the position can never be armed and — under the old
    ``abs()``-based detection — stayed flagged ``unhedged`` forever without ever
    being covered or flattened.

    ``BOTH`` (one-way mode) is never malformed: the sign legitimately encodes
    direction there, so the caller derives the side from the sign instead.
    """
    if side == "LONG":
        return position_amt < 0
    if side == "SHORT":
        return position_amt > 0
    return False


def detect_count_divergence(
    raw_journal_positions: dict[tuple[str, str], dict[str, Any]],
    accessor_positions: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """#587: flag a *count* mismatch between the raw local audit journal
    (``position_manager.positions``, read directly) and whatever
    ``position_manager.get_positions()`` currently returns.

    This is deliberately independent of :func:`detect_divergences`, which
    compares ``binance_positions`` against the *accessor's* output
    (``get_positions()``) — that comparison is already exchange-vs-exchange
    (and reads clean) once ``TE_EXCHANGE_TRUTH_STORE_ENABLED=on``, so it
    cannot see drift in the raw journal itself. #587's root cause was
    exactly this: ``/state`` read ``position_manager.positions`` directly
    (13 stale entries) while ``/positions`` read ``get_positions()`` (1
    exchange-authoritative entry) — two call sites, two different counts,
    with no per-symbol divergence ever raised because neither compared the
    raw journal against the accessor. This check closes that gap so any
    future direct read of ``.positions`` is monitored against the
    accessor's view, regardless of which flag mode is active.

    Returns a single structured divergence record when the counts differ,
    else ``None``.
    """
    raw_count = sum(
        1
        for pos in raw_journal_positions.values()
        if float(pos.get("quantity", pos.get("amount", 0)) or 0) != 0
    )
    accessor_count = len(accessor_positions)
    if raw_count == accessor_count:
        return None
    return {
        "category": "raw_journal_count_mismatch",
        "symbol": "ALL",
        "side": "ALL",
        "binance_qty": float(accessor_count),
        "local_qty": float(raw_count),
        "detail": (
            f"Raw local position journal has {raw_count} non-zero entries but "
            f"position_manager.get_positions() (the exchange-authoritative "
            f"accessor when enabled) reports {accessor_count} — any code path "
            "still reading .positions directly may report a stale count (#587)"
        ),
    }


def classify_verdict(divergences: list[dict[str, Any]]) -> str:
    """#592: classify the execution-evaluator verdict for this cycle.

    - no divergences -> ``"healthy"``
    - every divergence category is in ``_DEGRADED_CATEGORIES`` (``ghost``,
      ``raw_journal_count_mismatch``) -> ``"degraded"``. These are
      journal-only/local-bookkeeping issues: `ghost` is auto-voided by
      :class:`tradeengine.ghost_position_remediator.GhostPositionRemediator`
      and `raw_journal_count_mismatch` is purely diagnostic (see the
      runbook) — neither is evidence of unsafe exchange state, so a single
      unresolved occurrence must not hard-block intake.
    - any other category present (``untracked``, ``mutation``,
      ``unhedged``, ``malformed_position``) -> ``"unhealthy"`` (unchanged
      pre-#592 behavior — these DO indicate real exchange-state risk).
    """
    if not divergences:
        return "healthy"
    categories = {d["category"] for d in divergences}
    if categories <= _DEGRADED_CATEGORIES:
        return "degraded"
    return "unhealthy"


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
        raw_amt = float(bp.get("positionAmt", 0) or 0.0)

        # #547: a sign/side mismatch (LONG amt<0 or SHORT amt>0) is a malformed
        # hedge-mode state. A reduceOnly SL/TP derived from the declared side is
        # direction-invalid, so this position can never be armed. Classify it as
        # `malformed_position` and let the remediator take a safe terminal
        # action (flatten in arm_or_flatten; CRITICAL alert in arm_only) instead
        # of silently abs()-ing the sign and looping on `unhedged` forever.
        if _is_malformed_sign(side, raw_amt):
            divergences.append(
                {
                    "category": "malformed_position",
                    "symbol": symbol,
                    "side": side,
                    "binance_qty": abs(raw_amt),
                    "raw_position_amt": raw_amt,
                    "local_qty": 0.0,
                    "sl_present": False,
                    "tp_present": False,
                    "detail": (
                        f"Malformed hedge-mode position: positionSide={side} "
                        f"but positionAmt={raw_amt} (sign/side mismatch) — "
                        f"cannot be armed; requires flatten or alert"
                    ),
                }
            )
            continue

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
            # Per #594: Binance's /openAlgoOrders response uses "orderType" instead of "type"
            # for conditional orders. Add fallback so SL/TP are correctly detected.
            o_type = str(
                o.get("type") or o.get("orderType") or o.get("origType") or ""
            ).upper()
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
        ghost_remediator: GhostPositionRemediator | None = None,
        stream_consumer: Any | None = None,
    ) -> None:
        self._exchange = exchange
        self._position_manager = position_manager
        self._interval = interval_seconds
        self._remediator = remediator
        self._store = store
        # #609: the UserDataStreamConsumer whose store this reconciler is
        # backstopping. When the store goes stale (below), force_reconnect()
        # is the active fix — logging alone (pre-#609 behavior) never
        # recovered a connection that reports connected but has silently
        # stopped delivering events, since nothing inside the consumer's own
        # loop can notice a connection that neither closes nor raises.
        self._stream_consumer = stream_consumer
        self._last_forced_reconnect_at: datetime | None = None
        # #592: ghost-position write path. Defaults to an always-on
        # GhostPositionRemediator (mode="void") rather than None so this
        # ticket's fix is effective even for callers that don't thread the
        # new kwarg through explicitly — production wiring (api.py) passes
        # one built from settings.ghost_position_remediation_mode.
        self._ghost_remediator = ghost_remediator or GhostPositionRemediator(
            position_manager=position_manager
        )
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._last_divergence_count: int = 0
        # #592: last computed verdict string ("healthy" | "degraded" |
        # "unhealthy"), surfaced via health_check() and the admin
        # force-reconcile endpoint.
        self._last_verdict: str = "healthy"
        # #566: expected hedge-mode assumption baked into _normalise_side /
        # _is_malformed_sign. Compared each cycle against the account's
        # actual dualSidePosition setting via verify_hedge_mode().
        self._expected_hedge_mode: bool = HEDGE_MODE_ENABLED
        self._hedge_mode_mismatch_alerted: bool = False

    @property
    def last_verdict(self) -> str:
        """#592: verdict computed by the most recent reconcile_once() pass."""
        return self._last_verdict

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

    async def _check_hedge_mode(self) -> None:
        """#566: confirm the account's actual hedge-mode setting matches the
        assumption baked into ``_normalise_side``/``_is_malformed_sign``.

        Never raises — a failed or inconclusive check must not interrupt the
        read-only reconciliation pass. Alerts CRITICAL once per mismatch
        episode (latched) rather than every cycle.
        """
        try:
            verify = getattr(self._exchange, "verify_hedge_mode", None)
            if verify is None:
                return
            result = await verify()
        except Exception:
            logger.debug(
                "PositionReconciler: verify_hedge_mode() unavailable/failed — "
                "skipping hedge-mode confirmation this cycle",
                exc_info=True,
            )
            return

        if not isinstance(result, dict) or result.get("position_mode") == "unknown":
            # Inconclusive (API error surfaced by verify_hedge_mode itself) —
            # do not flip the gauge on a transient failure.
            return

        actual_hedge_mode = bool(result.get("hedge_mode_enabled", False))
        if actual_hedge_mode == self._expected_hedge_mode:
            hedge_mode_mismatch.set(0)
            self._hedge_mode_mismatch_alerted = False
            return

        hedge_mode_mismatch.set(1)
        if not self._hedge_mode_mismatch_alerted:
            self._hedge_mode_mismatch_alerted = True
            logger.critical(
                "PositionReconciler: hedge-mode MISMATCH — account "
                "dualSidePosition=%s (%s) but engine assumes hedge_mode=%s. "
                "positionSide/positionAmt classification "
                "(_normalise_side/_is_malformed_sign) may be misinterpreting "
                "the exchange contract for every position row. Confirm the "
                "testnet account's position mode setting matches "
                "HEDGE_MODE_ENABLED/POSITION_MODE (#566).",
                actual_hedge_mode,
                result.get("position_mode"),
                self._expected_hedge_mode,
            )

    async def _maybe_force_stream_reconnect(
        self, stale_secs: float, stale_threshold: float
    ) -> None:
        """#609 AC2: force the user-data stream to reconnect when it has been
        stale beyond ``stale_threshold``, rate-limited to at most once per
        :data:`_STREAM_RECONNECT_COOLDOWN_SECS`.

        No-op when no ``stream_consumer`` was injected (e.g. tests, or a
        deployment that hasn't wired one) or when a reconnect was already
        forced within the cooldown window. Never raises — this is a
        best-effort recovery action layered on top of the read-only
        reconciliation pass.

        The cooldown is only armed on a CONFIRMED close (``force_reconnect()``
        returning True). If the close attempt itself fails — no-op because
        the stream isn't connected, or the underlying close() call raised —
        the stream is still stuck, so the next reconcile cycle
        (``interval_seconds``, not the 5-minute cooldown) retries instead of
        silently suppressing recovery for 5 minutes.
        """
        if self._stream_consumer is None:
            return
        now = datetime.now(UTC)
        if (
            self._last_forced_reconnect_at is not None
            and (now - self._last_forced_reconnect_at).total_seconds()
            < _STREAM_RECONNECT_COOLDOWN_SECS
        ):
            return
        try:
            triggered = await self._stream_consumer.force_reconnect(
                reason=(
                    f"ExchangeTruthStore stream stale {stale_secs:.0f}s "
                    f"(threshold={stale_threshold:.0f}s)"
                )
            )
            if triggered:
                self._last_forced_reconnect_at = now
            else:
                logger.debug(
                    "PositionReconciler: force_reconnect() did not actually "
                    "close a connection — will retry next reconcile cycle "
                    "instead of arming the cooldown"
                )
        except Exception:
            logger.exception(
                "PositionReconciler: force_reconnect on stale stream failed"
            )

    async def reconcile_once(self) -> list[dict[str, Any]]:
        """Run one reconciliation pass; return the divergence list."""
        # #566: verify the hedge-mode assumption before classifying rows —
        # independent of the fetch/divergence pipeline below so a failure
        # here never blocks reconciliation.
        await self._check_hedge_mode()

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

        # #587: independent count-level check against the raw local audit
        # journal (see detect_count_divergence docstring for why this is not
        # redundant with detect_divergences above).
        count_divergence = detect_count_divergence(
            self._position_manager.positions, local_positions
        )
        if count_divergence is not None:
            divergences.append(count_divergence)
            raw_journal_count_mismatch.set(1)
        else:
            raw_journal_count_mismatch.set(0)

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

        # #592: hand `ghost` divergences to the write-mode ghost remediator
        # BEFORE computing the verdict, so a voided entry's `resolution` is
        # already attached to the divergence dict this function returns.
        # Never poisons the read-only pass on failure — same guard pattern
        # as the unhedged/malformed remediator call below.
        ghost_divergences = [d for d in divergences if d.get("category") == "ghost"]
        if ghost_divergences:
            try:
                await self._ghost_remediator.remediate(ghost_divergences)
            except Exception:
                logger.exception(
                    "PositionReconciler: ghost_remediator raised — read-only "
                    "reconciliation pass continues"
                )

        verdict = classify_verdict(divergences)
        self._last_verdict = verdict

        if divergences:
            self._log_divergence_summary(divergences, verdict)
        else:
            reconciliation_evaluator_verdict.set(0)
            reconciliation_alert.set(0)
            reconciliation_verdict_state.set(_VERDICT_STATE_VALUES["healthy"])
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
                stale_threshold = 2 * self._interval
                if stale_secs > stale_threshold:
                    logger.warning(
                        "ExchangeTruthStore stream stale: %.0fs (threshold=%ds)",
                        stale_secs,
                        stale_threshold,
                    )
                    # #609 AC2: actually force the reconnect the log line
                    # above used to just describe. Rate-limited so a
                    # persistently-stale stream is retried at most once per
                    # cooldown window instead of every reconcile cycle.
                    await self._maybe_force_stream_reconnect(
                        stale_secs, stale_threshold
                    )

        # #445: hand the unhedged subset to the write-mode remediator.
        # When mode == "off" (default), this is a no-op. The remediator
        # owns its own metrics/logging; failures here must not poison
        # the read-only reconciliation pass.
        if self._remediator is not None:
            # #547: malformed_position divergences must also reach the
            # remediator so it can flatten (arm_or_flatten) or alert (arm_only)
            # instead of the position staying naked forever.
            remediable = [
                d
                for d in divergences
                if d.get("category") in ("unhedged", "malformed_position")
            ]
            try:
                await self._remediator.remediate(
                    remediable, binance_positions=binance_positions
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

    def _log_divergence_summary(
        self, divergences: list[dict[str, Any]], verdict: str
    ) -> None:
        """AC3 + AC4 (+ #592 ternary verdict): set the evaluator/alert gauges
        and log the divergence summary, including any `resolution` recorded
        by the ghost remediator.
        """
        summary = "; ".join(
            f"{d['category']}:{d['symbol']}:{d['side']}"
            + (f"[{d['resolution']}]" if d.get("resolution") else "")
            for d in divergences
        )
        # FR66 category e: the alert still fires for ANY divergence,
        # degraded or unhealthy, so operators/the runbook stay in the loop
        # even when intake is not hard-blocked.
        reconciliation_alert.set(1)
        # Backwards-compatible binary gauge: only the hard-unhealthy verdict
        # sets it to 1, so a `degraded` cycle (per #592) does not trip
        # existing alert rules keyed on ==1.
        reconciliation_evaluator_verdict.set(1 if verdict == "unhealthy" else 0)
        reconciliation_verdict_state.set(_VERDICT_STATE_VALUES[verdict])
        log_fn = logger.warning if verdict == "unhealthy" else logger.info
        log_fn(
            "PositionReconciler: %d divergence(s) — evaluator.execution.verdict=%s. %s",
            len(divergences),
            verdict,
            summary,
        )

    # ------------------------------------------------------------------
    # Health check (duck-typed for dispatcher.health_check)
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        divergence_count = self._last_divergence_count
        return {
            "status": self._last_verdict,
            "divergence_count": divergence_count,
            "interval_seconds": self._interval,
        }
