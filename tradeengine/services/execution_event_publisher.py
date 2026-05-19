"""
Execution event publisher (PetroSa2/petrosa_k8s#586, P0.2c).

Emits one NATS message per order lifecycle event on the subject
``execution.events.<strategy_id>``. The prefix (`execution.events`) is sourced
from settings.nats_topic_execution_events; strategy_id is appended at publish
time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Literal

import nats
import nats.aio.client
from opentelemetry import trace
from prometheus_client import Counter

from shared.config import settings
from shared.constants import UTC

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


EventType = Literal["placed", "filled", "rejected", "partial_fill"]
_VALID_EVENT_TYPES: set[str] = {"placed", "filled", "rejected", "partial_fill"}


execution_events_published = Counter(
    "tradeengine_execution_events_published_total",
    "Total execution lifecycle events published to NATS",
    ["event_type", "strategy_id", "result"],
)


class ExecutionEventPublisher:
    """Lazy-connect publisher for execution.events.<strategy_id>.

    Owns its own NATS connection so it stays available even when the consumer
    is disabled (e.g. one-shot HTTP-driven flows in tests).
    """

    def __init__(self) -> None:
        self._nc: nats.aio.client.Client | None = None
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self) -> nats.aio.client.Client | None:
        if not settings.nats_enabled or not settings.nats_servers:
            return None
        if self._nc is not None and self._nc.is_connected:
            return self._nc
        async with self._connect_lock:
            if self._nc is not None and self._nc.is_connected:
                return self._nc
            from shared.constants import (
                NATS_CONNECT_TIMEOUT,
                NATS_MAX_RECONNECT_ATTEMPTS,
                NATS_RECONNECT_TIME_WAIT,
            )

            try:
                self._nc = await nats.connect(
                    servers=settings.nats_servers,
                    connect_timeout=NATS_CONNECT_TIMEOUT,
                    max_reconnect_attempts=NATS_MAX_RECONNECT_ATTEMPTS,
                    reconnect_time_wait=NATS_RECONNECT_TIME_WAIT,
                    allow_reconnect=True,
                    name="petrosa-tradeengine-execution-events",
                )
                logger.info(
                    "ExecutionEventPublisher connected to NATS at %s",
                    settings.nats_servers,
                )
            except Exception as e:
                logger.error("ExecutionEventPublisher failed to connect to NATS: %s", e)
                self._nc = None
        return self._nc

    def set_client(self, nc: nats.aio.client.Client | None) -> None:
        """Inject an existing NATS client (e.g. consumer's) to avoid double-connect."""
        self._nc = nc

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception:
                pass
            self._nc = None

    @staticmethod
    def _build_subject(strategy_id: str) -> str:
        prefix = settings.nats_topic_execution_events or "execution.events"
        # If operator overrode env to a wildcard subscription pattern, strip it.
        prefix = prefix.rstrip(".*>").rstrip(".")
        safe_strategy = (strategy_id or "unknown").strip() or "unknown"
        return f"{prefix}.{safe_strategy}"

    @staticmethod
    def _build_payload(
        *,
        decision_id: str | None,
        strategy_id: str,
        order_id: str,
        event_type: EventType,
        reason: str,
        timestamp: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ts = (timestamp or datetime.now(UTC)).astimezone(UTC).isoformat()
        payload: dict[str, Any] = {
            "decision_id": decision_id or "",
            "strategy_id": strategy_id or "unknown",
            "order_id": order_id or "",
            "event_type": event_type,
            "timestamp": ts,
            "reason": reason or "",
        }
        if extra:
            # Skip keys that would clobber required fields.
            for k, v in extra.items():
                if k not in payload and v is not None:
                    payload[k] = v
        return payload

    async def publish(
        self,
        *,
        event_type: EventType,
        strategy_id: str,
        order_id: str,
        reason: str,
        decision_id: str | None = None,
        timestamp: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Emit one execution event. Returns True on success, False otherwise.

        Non-fatal — failures are logged but never raised; the order path must
        not break if the observability bus is unhealthy.
        """
        if event_type not in _VALID_EVENT_TYPES:
            logger.error("Refusing to publish unknown event_type=%s", event_type)
            return False

        subject = self._build_subject(strategy_id)
        payload = self._build_payload(
            decision_id=decision_id,
            strategy_id=strategy_id,
            order_id=order_id,
            event_type=event_type,
            reason=reason,
            timestamp=timestamp,
            extra=extra,
        )

        with tracer.start_as_current_span(
            "execution_event.publish",
            kind=trace.SpanKind.PRODUCER,
        ) as span:
            span.set_attribute("messaging.system", "nats")
            span.set_attribute("messaging.destination", subject)
            span.set_attribute("messaging.operation", "publish")
            span.set_attribute("execution.event_type", event_type)
            span.set_attribute("strategy.id", payload["strategy_id"])
            if decision_id:
                span.set_attribute("decision.decision_id", decision_id)
            if order_id:
                span.set_attribute("order.id", order_id)

            nc = await self._ensure_connected()
            if nc is None:
                # Publisher is best-effort: NATS-disabled mode is not an error.
                logger.info(
                    "execution_event.skipped subject=%s event_type=%s strategy_id=%s "
                    "order_id=%s decision_id=%s reason=%s (nats disabled or unavailable)",
                    subject,
                    event_type,
                    payload["strategy_id"],
                    order_id,
                    decision_id,
                    reason,
                )
                execution_events_published.labels(
                    event_type=event_type,
                    strategy_id=payload["strategy_id"],
                    result="skipped",
                ).inc()
                return False

            try:
                await nc.publish(subject, json.dumps(payload).encode())
                logger.info(
                    "execution_event.published subject=%s event_type=%s "
                    "strategy_id=%s order_id=%s decision_id=%s reason=%s",
                    subject,
                    event_type,
                    payload["strategy_id"],
                    order_id,
                    decision_id,
                    reason,
                )
                execution_events_published.labels(
                    event_type=event_type,
                    strategy_id=payload["strategy_id"],
                    result="ok",
                ).inc()
                span.set_status(trace.Status(trace.StatusCode.OK))
                return True
            except Exception as e:
                logger.error(
                    "execution_event.failed subject=%s event_type=%s error=%s",
                    subject,
                    event_type,
                    e,
                    exc_info=True,
                )
                execution_events_published.labels(
                    event_type=event_type,
                    strategy_id=payload["strategy_id"],
                    result="error",
                ).inc()
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                span.record_exception(e)
                return False


# Module-level singleton — dispatcher imports and calls this directly.
execution_event_publisher = ExecutionEventPublisher()
