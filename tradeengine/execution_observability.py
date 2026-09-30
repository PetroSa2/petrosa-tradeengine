"""Low-cardinality trade execution metrics and bounded summary logging."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import (
    Counter as ValueCounter,
    deque,
)
from collections.abc import Callable
from statistics import quantiles
from typing import Any

from prometheus_client import Counter, Histogram

trade_orders_total = Counter(
    "petrosa_trade_orders_total",
    "Trade orders completed by bounded outcome labels",
    ["side", "order_type", "outcome"],
)
trade_order_duration_seconds = Histogram(
    "petrosa_trade_order_duration_seconds",
    "Trade order execution duration by bounded operation labels",
    ["operation", "outcome"],
)

ALLOWED_LABELS = {
    "petrosa_trade_orders_total": frozenset({"side", "order_type", "outcome"}),
    "petrosa_trade_order_duration_seconds": frozenset({"operation", "outcome"}),
}
_OUTCOMES = frozenset({"accepted", "rejected", "error"})
_MAX_LATENCIES = 2048


def _bounded(value: Any, allowed: frozenset[str]) -> str:
    text = str(value or "unknown").lower()
    return text if text in allowed else "unknown"


class TradeExecutionObservability:
    """Records execution telemetry and emits one safe summary per window."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.logger = logger
        self.clock = clock
        self.wall_clock = wall_clock
        self.window_started = clock()
        self.outcomes: ValueCounter[str] = ValueCounter()
        self.protective_leg_actions = 0
        self.latencies: deque[float] = deque(maxlen=_MAX_LATENCIES)
        self._task: asyncio.Task[None] | None = None

    def record_order(
        self,
        *,
        side: Any,
        order_type: Any,
        outcome: Any,
        duration_seconds: float,
        operation: Any = "execute",
    ) -> None:
        bounded_outcome = _bounded(outcome, _OUTCOMES)
        bounded_side = _bounded(side, frozenset({"buy", "sell"}))
        bounded_type = _bounded(
            order_type, frozenset({"market", "limit", "stop", "stop_market", "unknown"})
        )
        bounded_operation = _bounded(
            operation, frozenset({"execute", "place", "cancel", "replace", "unknown"})
        )
        duration = max(0.0, float(duration_seconds))
        trade_orders_total.labels(bounded_side, bounded_type, bounded_outcome).inc()
        trade_order_duration_seconds.labels(bounded_operation, bounded_outcome).observe(
            duration
        )
        self.outcomes[bounded_outcome] += 1
        self.latencies.append(duration)

    def record_protective_leg_action(self) -> None:
        self.protective_leg_actions += 1

    def summary(self) -> dict[str, Any]:
        values = sorted(self.latencies)
        p50 = values[len(values) // 2] if values else 0.0
        p95 = quantiles(values, n=20)[18] if len(values) > 1 else p50
        return {
            "event": "SUMMARY",
            "window_seconds": 300,
            "service": "petrosa-tradeengine",
            "orders_by_outcome": {
                outcome: self.outcomes.get(outcome, 0) for outcome in sorted(_OUTCOMES)
            },
            "outcomes": sorted(self.outcomes),
            "protective_leg_actions": self.protective_leg_actions,
            "latency_p50_seconds": round(p50, 6),
            "latency_p95_seconds": round(p95, 6),
        }

    def emit_summary(self, *, force: bool = False) -> bool:
        if not force and self.clock() - self.window_started < 300:
            return False
        self.logger.info(json.dumps(self.summary(), separators=(",", ":")))
        self.window_started = self.clock()
        self.outcomes.clear()
        self.protective_leg_actions = 0
        self.latencies.clear()
        return True

    async def start(self) -> None:
        if self._task is not None:
            return

        async def emit_periodically() -> None:
            while True:
                await asyncio.sleep(300)
                self.emit_summary(force=True)

        self._task = asyncio.create_task(emit_periodically())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.emit_summary(force=True)
