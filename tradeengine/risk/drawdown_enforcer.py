"""FR30 — per-strategy drawdown enforcement (petrosa-tradeengine#431).

Three pieces:

* :func:`check_drawdown_breach` — pure comparator. Given the current observed
  drawdown for a strategy and the configured envelope value, decide whether
  a breach has occurred. No I/O. Easy to unit-test.

* :func:`get_stub_envelope_value` — temporary envelope source backed by
  ``settings.max_daily_loss_pct`` (the legacy per-account global value).
  When the envelope-fetcher wiring lands in [petrosa-tradeengine#421](https://github.com/PetroSa2/petrosa-tradeengine/issues/421)
  this stub is replaced with the HTTP client to data-manager's
  ``GET /api/envelopes/active/{key}`` — same function signature so the call
  sites in this leaf do not need to change.

* :class:`DrawdownBreachEmitter` — wraps the existing
  :class:`~tradeengine.services.alert_publisher.AlertPublisher` NATS
  connection but publishes to the cross-service subject
  ``alerts.drawdown.breach.{strategy_id}`` (matching the cio convention at
  ``petrosa-cio/cio/core/alerting/fr66_alerts.py:252``). **Envelope fields
  (``envelope_version``, ``envelope_source``) are NULL in this leaf** —
  those land in [petrosa-tradeengine#422](https://github.com/PetroSa2/petrosa-tradeengine/issues/422)'s
  schema extension.

The dispatcher integration is left to the existing per-tick reconciliation
path; this module exposes :func:`check_and_emit` as the integration point so
the dispatcher call site is one line.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shared.config import settings
from shared.constants import UTC

logger = logging.getLogger(__name__)

DRAWDOWN_BREACH_SUBJECT_PREFIX = "alerts.drawdown.breach"


@dataclass(frozen=True)
class Breach:
    """A drawdown-breach event prior to NATS publication."""

    strategy_id: str
    observed_drawdown_pct: float
    envelope_value_pct: float
    exceeded_by_pct: float
    detected_at: datetime


def check_drawdown_breach(
    *,
    strategy_id: str,
    observed_drawdown_pct: float,
    envelope_value_pct: float,
) -> Breach | None:
    """Return a :class:`Breach` iff ``observed_drawdown_pct > envelope_value_pct``.

    Inputs are percentages expressed as positive floats (e.g. ``5.0`` means
    5%). The comparator treats equality as "not yet breached" to give the
    operator one tick of grace before alerting — matches the convention in
    ``PortfolioTracker.would_breach_ceiling`` where the cluster passes the
    test until it strictly exceeds the ceiling.
    """
    if not strategy_id:
        return None
    if observed_drawdown_pct <= envelope_value_pct:
        return None
    return Breach(
        strategy_id=strategy_id,
        observed_drawdown_pct=float(observed_drawdown_pct),
        envelope_value_pct=float(envelope_value_pct),
        exceeded_by_pct=float(observed_drawdown_pct - envelope_value_pct),
        detected_at=datetime.now(UTC),
    )


def get_stub_envelope_value(strategy_id: str) -> float:
    """Return the configured envelope value for a strategy.

    **Stub source** — returns ``settings.max_daily_loss_pct`` regardless of
    ``strategy_id``. The full implementation in [petrosa-tradeengine#421](https://github.com/PetroSa2/petrosa-tradeengine/issues/421)
    swaps this for an HTTP call to ``petrosa-data-manager`` via the
    EnvelopeFetcher pattern from [petrosa-cio#155](https://github.com/PetroSa2/petrosa-cio/pull/155).

    Same signature so the call sites in this leaf are unchanged after #421
    lands. Returns 0.0 if the setting is unavailable.
    """
    _ = strategy_id  # Future: will key the lookup
    value = getattr(settings, "max_daily_loss_pct", None)
    if value is None:
        return 0.0
    return float(value)


class DrawdownBreachEmitter:
    """Publish drawdown-breach events to NATS subject ``alerts.drawdown.breach.{strategy_id}``.

    Uses the AlertPublisher's NATS client when present (avoiding a duplicate
    connection); falls back to "log only, do not raise" when NATS is
    unavailable. Never throws — the order/dispatch path must not break on
    an observability bus outage.
    """

    def __init__(self, alert_publisher: Any | None = None) -> None:
        self._alert_publisher = alert_publisher

    def set_alert_publisher(self, alert_publisher: Any) -> None:
        self._alert_publisher = alert_publisher

    async def emit(self, breach: Breach) -> bool:
        """Publish one breach event. Returns ``True`` iff the publish succeeded."""
        subject = f"{DRAWDOWN_BREACH_SUBJECT_PREFIX}.{breach.strategy_id}"
        payload: dict[str, Any] = {
            "strategy_id": breach.strategy_id,
            "observed_drawdown_pct": breach.observed_drawdown_pct,
            "envelope_value_pct": breach.envelope_value_pct,
            "exceeded_by_pct": breach.exceeded_by_pct,
            "detected_at": breach.detected_at.astimezone(UTC).isoformat(),
            # FR62 envelope fields land in #422; NULL here so the schema is
            # forward-compatible.
            "envelope_version": None,
            "envelope_source": None,
        }
        if self._alert_publisher is None:
            logger.info(
                "drawdown_breach.skipped subject=%s payload=%s (no AlertPublisher attached)",
                subject,
                payload,
            )
            return False
        nc = await self._alert_publisher._ensure_connected()  # noqa: SLF001
        if nc is None:
            logger.info(
                "drawdown_breach.skipped subject=%s (nats disabled or unavailable)",
                subject,
            )
            return False
        try:
            await nc.publish(subject, json.dumps(payload).encode("utf-8"))
            logger.warning(
                "drawdown_breach.published subject=%s strategy_id=%s exceeded_by=%.4f",
                subject,
                breach.strategy_id,
                breach.exceeded_by_pct,
            )
            return True
        except Exception as exc:  # pragma: no cover — defensive
            logger.error(
                "drawdown_breach.publish_failed subject=%s err=%s", subject, exc
            )
            return False


async def check_and_emit(
    *,
    strategy_id: str,
    observed_drawdown_pct: float,
    emitter: DrawdownBreachEmitter,
    envelope_value_pct: float | None = None,
) -> Breach | None:
    """Run the comparator and emit a breach event on positive detection.

    Dispatcher integration point — call this from the existing per-tick
    reconciliation loop (or order-acceptance path). Returns the breach
    that fired, or ``None`` when no breach was detected. The emitter
    failure mode (NATS down) is logged but not propagated.
    """
    if envelope_value_pct is None:
        envelope_value_pct = get_stub_envelope_value(strategy_id)
    breach = check_drawdown_breach(
        strategy_id=strategy_id,
        observed_drawdown_pct=observed_drawdown_pct,
        envelope_value_pct=envelope_value_pct,
    )
    if breach is not None:
        await emitter.emit(breach)
    return breach
