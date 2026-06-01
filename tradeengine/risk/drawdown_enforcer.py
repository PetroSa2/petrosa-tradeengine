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
    """Return the legacy stub envelope value for a strategy.

    Returns ``settings.max_daily_loss_pct`` regardless of ``strategy_id``.
    Used as the FALLBACK when the real envelope fetcher (#421) is not
    configured or the data-manager call fails — see
    :func:`get_envelope_value_for_strategy`.

    Returns 0.0 if the setting is unavailable.
    """
    _ = strategy_id
    value = getattr(settings, "max_daily_loss_pct", None)
    if value is None:
        return 0.0
    return float(value)


def _extract_envelope_value_pct(envelope: dict[str, Any]) -> float | None:
    """Pull the comparator value out of a data-manager envelope dict.

    Looks at ``envelope["value"]["max_drawdown_pct"]`` per the schema cio
    uses (see ``petrosa-cio/tests/unit/test_envelope_fetcher.py``). Returns
    ``None`` if the field is missing or not a number — caller falls back
    to the stub.
    """
    value_obj = envelope.get("value") if isinstance(envelope, dict) else None
    if not isinstance(value_obj, dict):
        return None
    raw = value_obj.get("max_drawdown_pct")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def get_envelope_value_for_strategy(
    strategy_id: str,
) -> tuple[float, dict[str, Any] | None]:
    """Fetch the active envelope for a strategy and return (value_pct, envelope).

    AC3.d of P4.6 (FR62, #421): the FR30 comparator reads the active
    envelope via the same HTTP client contract as cio (see
    :mod:`tradeengine.services.envelope_fetcher`). When the fetcher is
    unconfigured, errors out, or returns an envelope without a parseable
    ``max_drawdown_pct``, the function falls back to the legacy stub
    (``settings.max_daily_loss_pct``) and returns ``(stub_value, None)``.

    Returns a 2-tuple so the caller can also surface the envelope's
    ``version`` and ``source`` metadata (deferred to #422's schema
    extension — this leaf just exposes the source).
    """
    # Local import to avoid a startup-time cycle: envelope_fetcher imports
    # httpx eagerly and we want this module importable even in tooling that
    # doesn't have the HTTP stack wired.
    from tradeengine.services.envelope_fetcher import (
        EnvelopeFetchError,
        EnvelopeNotFoundError,
        get_envelope_fetcher,
        strategy_key,
    )

    fetcher = get_envelope_fetcher()
    if fetcher is None:
        return get_stub_envelope_value(strategy_id), None

    try:
        envelope = await fetcher.get_active(strategy_key(strategy_id))
    except EnvelopeNotFoundError:
        # AC3.b lives in cio (the admission refuses the order). Here in
        # the tradeengine drawdown path, "no envelope" still has to make
        # a decision — fall back to the legacy stub so the comparator
        # keeps working under partial-deploy / pre-onboarding conditions.
        logger.info(
            "envelope_fallback_no_envelope",
            extra={"strategy_id": strategy_id},
        )
        return get_stub_envelope_value(strategy_id), None
    except EnvelopeFetchError as exc:
        logger.warning(
            "envelope_fallback_fetch_error",
            extra={"strategy_id": strategy_id, "error": str(exc)},
        )
        return get_stub_envelope_value(strategy_id), None

    value_pct = _extract_envelope_value_pct(envelope)
    if value_pct is None:
        logger.warning(
            "envelope_fallback_missing_max_drawdown_pct",
            extra={"strategy_id": strategy_id, "envelope": envelope},
        )
        return get_stub_envelope_value(strategy_id), envelope
    return value_pct, envelope


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

    Envelope source (#421 / P4.6-AC3.d): when ``envelope_value_pct`` is
    not supplied, the function fetches the active envelope from
    data-manager via :func:`get_envelope_value_for_strategy` (no drift
    with cio's EnvelopeFetcher contract). When the fetcher is unconfigured
    or the call fails, it transparently falls back to the legacy stub.
    """
    if envelope_value_pct is None:
        envelope_value_pct, _envelope = await get_envelope_value_for_strategy(
            strategy_id
        )
    breach = check_drawdown_breach(
        strategy_id=strategy_id,
        observed_drawdown_pct=observed_drawdown_pct,
        envelope_value_pct=envelope_value_pct,
    )
    if breach is not None:
        await emitter.emit(breach)
    return breach
