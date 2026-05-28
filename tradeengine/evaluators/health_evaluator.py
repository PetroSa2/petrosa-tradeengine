"""tradeengine health evaluator (P2.7, petrosa_k8s#697 AC5 / #417).

Emits ``evaluator.tradeengine.verdict`` via the shared P2.1 framework
(:mod:`petrosa_otel.evaluators`) so the operator dashboard's evaluator strip
counts tradeengine among the reporting subsystems (FR17 / FR23 / FR32).

The verdict is sourced **read-only** from existing Prometheus instruments
already exported by tradeengine — this module deliberately does not touch the
order / risk / position-tracking code. Three signals are sampled on each tick:

1. **Pre-trade-check pass rate** — `tradeengine_risk_rejections_total` /
   `tradeengine_risk_checks_total`. A sustained high rejection rate (per #404
   the realistic failure is *all* checks failing → 0% pass rate) trips the
   verdict; a small delta below the minimum-volume guard keeps the check
   dormant on quiet windows.
2. **Order-placement latency** — `tradeengine_order_execution_latency_seconds`
   histogram. Mean over the tick (`Δsum / Δcount`) above the threshold trips
   the verdict.
3. **Position-tracker consistency** — `tradeengine_position_reconciliation_
   divergences_total` (FR65). Any sustained increase per tick means the local
   position state has drifted from Binance.

Verdict vocabulary is the framework's locked three-state contract
(``healthy`` / ``unhealthy`` / ``unknown``); any breached signal maps to
``unhealthy`` with a single-line reason naming the tripped signal (NFR-O5).

Hysteresis / cadence (AC4 / AC7, FR18 per-evaluator ``decision_window``):
emits every ``EMIT_INTERVAL_S`` (15s) with `ConsecutiveSamplesHysteresis(n=3)`
→ ~45s decision window. Documented at module scope to keep the verdict stable
under bursty traffic while a sustained problem surfaces within ~45s.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - py310 compatibility
    from datetime import timezone

    UTC = timezone.utc  # noqa: UP017

import nats
from petrosa_otel.evaluators import (
    ConsecutiveSamplesHysteresis,
    Evaluator,
    NatsVerdictPublisher,
)

if TYPE_CHECKING:
    from petrosa_otel.evaluators.base import HysteresisPolicy
    from petrosa_otel.evaluators.publisher import VerdictPublisher

logger = logging.getLogger(__name__)

SUBSYSTEM = "tradeengine"

# Cadence + smoothing (documented per AC4 / AC7).
EMIT_INTERVAL_S = 15.0
HYSTERESIS_SAMPLES = 3

# Pre-trade-check: rejection rate above this fraction over a tick trips
# unhealthy. 0.5 is conservative — catches the #404 zero-pass-rate case (100%
# rejections) without flapping on a handful of normal rejections.
DEFAULT_REJECTION_RATE_THRESHOLD = 0.5
# Minimum risk-check delta within a tick before the rejection-rate check may
# trip. Avoids false positives at very low order volume.
DEFAULT_MIN_RISK_CHECKS = 4
# Order-placement latency: mean per tick above this trips unhealthy. Binance
# orders should complete in ≤ a couple of seconds; sustained >10s signals
# exchange/network degradation rather than a single slow order.
DEFAULT_LATENCY_THRESHOLD_S = 10.0
# Rolling window for the rejection-rate baseline (8 ticks ≈ 2 min at 15s).
DEFAULT_BASELINE_WINDOW = 8


def _counter_total(metric: Any) -> float:
    """Sum a Prometheus Counter across all label permutations."""
    total = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_total"):
                total += sample.value
    return total


def _histogram_sum_count(metric: Any) -> tuple[float, float]:
    """Return ``(sum, count)`` aggregated across all label permutations."""
    s = 0.0
    c = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_sum"):
                s += sample.value
            elif sample.name.endswith("_count"):
                c += sample.value
    return s, c


class TradeEngineHealthEvaluator(Evaluator):
    """Subsystem evaluator for tradeengine risk/latency/position health."""

    def __init__(
        self,
        *,
        metrics_source: Callable[[], dict[str, Any]],
        publisher: VerdictPublisher | None = None,
        nats_servers: str | None = None,
        hysteresis: HysteresisPolicy | None = None,
        rejection_rate_threshold: float = DEFAULT_REJECTION_RATE_THRESHOLD,
        min_risk_checks: int = DEFAULT_MIN_RISK_CHECKS,
        latency_threshold_s: float = DEFAULT_LATENCY_THRESHOLD_S,
        baseline_window: int = DEFAULT_BASELINE_WINDOW,
        emit_interval_s: float = EMIT_INTERVAL_S,
        time_source: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            subsystem=SUBSYSTEM,
            publisher=publisher,
            hysteresis=hysteresis or ConsecutiveSamplesHysteresis(n=HYSTERESIS_SAMPLES),
        )
        self._metrics_source = metrics_source
        self._nats_servers = nats_servers
        self._owns_publisher = publisher is None and nats_servers is not None
        self._rejection_rate_threshold = rejection_rate_threshold
        self._min_risk_checks = max(1, min_risk_checks)
        self._latency_threshold_s = latency_threshold_s
        self._emit_interval_s = emit_interval_s
        self._time = time_source or (lambda: datetime.now(UTC))

        self._rejection_rate_baseline: deque[float] = deque(
            maxlen=max(1, baseline_window)
        )
        self._prev_checks: float | None = None
        self._prev_rejections: float = 0.0
        self._prev_latency_sum: float = 0.0
        self._prev_latency_count: float = 0.0
        self._prev_divergences: float = 0.0
        self._prev_sample_at: datetime | None = None

        self._own_nc: nats.aio.client.Client | None = None
        self._emit_task: asyncio.Task[Any] | None = None

    # ----- lifecycle -----

    async def start(self) -> None:
        """Connect (if owning the publisher) and start the periodic emit loop."""
        if self._emit_task is not None:
            return
        if self._owns_publisher and self._publisher is None and self._nats_servers:
            try:
                self._own_nc = await nats.connect(
                    servers=self._nats_servers,
                    name="petrosa-tradeengine-evaluator",
                    allow_reconnect=True,
                )
                self._publisher = NatsVerdictPublisher(nats_client=self._own_nc)
                logger.info(
                    "tradeengine_health_evaluator NATS connected",
                    extra={"servers": self._nats_servers},
                )
            except Exception as exc:  # noqa: BLE001 — degrade, never crash startup
                logger.warning(
                    "tradeengine_health_evaluator NATS connect failed: %s — "
                    "evaluator will tick without publishing",
                    exc,
                )
                self._own_nc = None
        self._emit_task = asyncio.create_task(self._emit_loop())
        logger.info(
            "tradeengine_health_evaluator_started",
            extra={"subsystem": SUBSYSTEM, "emit_interval_s": self._emit_interval_s},
        )

    async def stop(self) -> None:
        """Stop the emit loop and close the owned NATS connection (if any)."""
        if self._emit_task is not None:
            self._emit_task.cancel()
            try:
                await self._emit_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"tradeengine_health_evaluator stop error: {exc}")
            self._emit_task = None
        if self._own_nc is not None:
            try:
                await self._own_nc.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"tradeengine_health_evaluator close error: {exc}")
            self._own_nc = None
            self._publisher = None

    async def _emit_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 — never crash the loop
                logger.warning(
                    "tradeengine_health_evaluator_tick_failed",
                    extra={"error": str(exc)},
                )
            await asyncio.sleep(self._emit_interval_s)

    # ----- framework hook -----

    async def evaluate(self) -> tuple[str, str]:
        """Compute the raw ``(verdict, reason)`` sample for the current state."""
        snapshot = self._metrics_source()
        now = self._time()

        checks = float(snapshot.get("risk_checks", 0.0) or 0.0)
        rejections = float(snapshot.get("risk_rejections", 0.0) or 0.0)
        latency_sum = float(snapshot.get("order_latency_sum", 0.0) or 0.0)
        latency_count = float(snapshot.get("order_latency_count", 0.0) or 0.0)
        divergences = float(snapshot.get("divergences", 0.0) or 0.0)

        prev_checks = self._prev_checks
        prev_rejections = self._prev_rejections
        prev_latency_sum = self._prev_latency_sum
        prev_latency_count = self._prev_latency_count
        prev_divergences = self._prev_divergences
        prev_at = self._prev_sample_at

        self._prev_checks = checks
        self._prev_rejections = rejections
        self._prev_latency_sum = latency_sum
        self._prev_latency_count = latency_count
        self._prev_divergences = divergences
        self._prev_sample_at = now

        if prev_checks is None or prev_at is None:
            return "unknown", "establishing baseline (first sample)"

        if (
            checks < prev_checks
            or rejections < prev_rejections
            or latency_sum < prev_latency_sum
            or latency_count < prev_latency_count
            or divergences < prev_divergences
        ):
            self._rejection_rate_baseline.clear()
            return "unknown", "counter reset detected; rebaselining"

        d_divergences = divergences - prev_divergences
        d_checks = checks - prev_checks
        d_rejections = rejections - prev_rejections
        d_latency_sum = latency_sum - prev_latency_sum
        d_latency_count = latency_count - prev_latency_count

        # 1) Position-tracker consistency — any new divergence is unhealthy.
        if d_divergences > 0:
            return (
                "unhealthy",
                f"position-tracker divergence: +{int(d_divergences)} in last "
                f"{int(self._emit_interval_s)}s",
            )

        # 2) Order-placement latency.
        if d_latency_count > 0:
            mean_latency = d_latency_sum / d_latency_count
            if mean_latency > self._latency_threshold_s:
                return (
                    "unhealthy",
                    f"order-placement latency {mean_latency:.2f}s mean > "
                    f"{self._latency_threshold_s:.1f}s threshold",
                )

        # 3) Pre-trade-check pass rate.
        if d_checks >= self._min_risk_checks:
            rejection_rate = d_rejections / d_checks if d_checks > 0 else 0.0
            self._rejection_rate_baseline.append(rejection_rate)
            if rejection_rate > self._rejection_rate_threshold:
                pass_pct = (1.0 - rejection_rate) * 100
                return (
                    "unhealthy",
                    f"pre-trade-check pass rate {pass_pct:.0f}% — "
                    f"{int(d_rejections)}/{int(d_checks)} rejected",
                )

        return (
            "healthy",
            f"checks {int(d_checks)}, rejections {int(d_rejections)}, "
            f"latency_count {int(d_latency_count)}",
        )


def build_tradeengine_health_evaluator(
    *,
    nats_servers: str | None = None,
) -> TradeEngineHealthEvaluator | None:
    """Construct an evaluator wired to tradeengine's existing Prometheus metrics.

    The evaluator owns a dedicated NATS connection for publishing verdicts
    (lazy: opened in ``start()``; closed in ``stop()``). Returns ``None`` when
    ``nats_servers`` is empty (NATS disabled).
    """
    if not nats_servers:
        logger.info(
            "tradeengine_health_evaluator not started: NATS disabled (no servers)"
        )
        return None

    # Import metric objects lazily so this module is importable without
    # tradeengine.metrics side effects in tests.
    from tradeengine.metrics import (
        order_execution_latency_seconds,
        risk_checks_total,
        risk_rejections_total,
    )
    from tradeengine.position_reconciler import reconciliation_divergences_total

    def _snapshot() -> dict[str, Any]:
        latency_sum, latency_count = _histogram_sum_count(
            order_execution_latency_seconds
        )
        return {
            "risk_checks": _counter_total(risk_checks_total),
            "risk_rejections": _counter_total(risk_rejections_total),
            "order_latency_sum": latency_sum,
            "order_latency_count": latency_count,
            "divergences": _counter_total(reconciliation_divergences_total),
        }

    return TradeEngineHealthEvaluator(
        metrics_source=_snapshot,
        nats_servers=nats_servers,
    )
