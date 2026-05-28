"""Alert publisher for tradeengine operational alerts.

Emits messages on the ``alerts.tradeengine.<event>`` family of NATS subjects.
Best-effort: failures are logged but never raise into the caller (the order
or rejection path must not break if the observability bus is unhealthy).

This mirrors the shape of :mod:`tradeengine.services.execution_event_publisher`
intentionally — same lazy-connect, same NATS-disabled bail-out semantics —
so operators can reason about one publishing pattern, not two.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import nats
import nats.aio.client
from opentelemetry import trace
from prometheus_client import Counter

from shared.config import settings
from shared.constants import UTC

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


tradeengine_alerts_published = Counter(
    "tradeengine_alerts_published_total",
    "Total operational alerts published to NATS",
    ["alert_name", "severity", "result"],
)


class AlertPublisher:
    """Lazy-connect publisher for ``alerts.tradeengine.<event>`` subjects."""

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
                    name="petrosa-tradeengine-alerts",
                )
                logger.info(
                    "AlertPublisher connected to NATS at %s",
                    settings.nats_servers,
                )
            except Exception as e:
                logger.error("AlertPublisher failed to connect to NATS: %s", e)
                self._nc = None
        return self._nc

    def set_client(self, nc: nats.aio.client.Client | None) -> None:
        """Inject an existing NATS client (e.g. the dispatcher's) to avoid double-connect."""
        self._nc = nc

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception:
                pass
            self._nc = None

    async def publish(
        self,
        *,
        alert_name: str,
        severity: str,
        payload: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> bool:
        """Emit one alert. Returns True on success, False otherwise.

        Never raises — the alert path must not break the caller (rejection
        / fill flow). NATS-disabled mode is a non-error skip.
        """
        if not alert_name:
            logger.error("Refusing to publish alert with empty alert_name")
            return False

        subject = f"alerts.tradeengine.{alert_name}"
        ts = (timestamp or datetime.now(UTC)).astimezone(UTC).isoformat()
        body: dict[str, Any] = {
            "alert_name": alert_name,
            "severity": severity,
            "timestamp": ts,
            **payload,
        }

        with tracer.start_as_current_span(
            "tradeengine_alert.publish",
            kind=trace.SpanKind.PRODUCER,
        ) as span:
            span.set_attribute("messaging.system", "nats")
            span.set_attribute("messaging.destination", subject)
            span.set_attribute("messaging.operation", "publish")
            span.set_attribute("alert.name", alert_name)
            span.set_attribute("alert.severity", severity)

            nc = await self._ensure_connected()
            if nc is None:
                logger.info(
                    "tradeengine_alert.skipped subject=%s severity=%s "
                    "(nats disabled or unavailable)",
                    subject,
                    severity,
                )
                tradeengine_alerts_published.labels(
                    alert_name=alert_name,
                    severity=severity,
                    result="skipped",
                ).inc()
                return False

            try:
                await nc.publish(subject, json.dumps(body).encode())
                logger.info(
                    "tradeengine_alert.published subject=%s severity=%s",
                    subject,
                    severity,
                )
                tradeengine_alerts_published.labels(
                    alert_name=alert_name,
                    severity=severity,
                    result="ok",
                ).inc()
                span.set_status(trace.Status(trace.StatusCode.OK))
                return True
            except Exception as e:
                logger.error(
                    "tradeengine_alert.failed subject=%s severity=%s error=%s",
                    subject,
                    severity,
                    e,
                    exc_info=True,
                )
                tradeengine_alerts_published.labels(
                    alert_name=alert_name,
                    severity=severity,
                    result="error",
                ).inc()
                span.set_status(
                    trace.Status(trace.StatusCode.ERROR, description=str(e))
                )
                return False


alert_publisher = AlertPublisher()
