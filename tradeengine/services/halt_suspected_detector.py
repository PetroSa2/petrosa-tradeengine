"""Tradeengine halt-suspected detector (#419, FR66 extension).

Watches the rejection stream for the pattern that produced the silent
9-hour halt on 2026-05-22→23 (root-caused in #404 / fixed in #406): a
burst of ``source=balance`` rejections with no successful order in
between. When that pattern crosses the configured threshold the detector
emits ``alerts.tradeengine.halt_suspected``; a single non-balance-rejected
completion clears the state and emits ``alerts.tradeengine.halt_cleared``.

The detector is wired into the two centralized event-emit helpers in
``tradeengine.dispatcher`` so every rejection / fill path feeds it
exactly once.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from shared.constants import UTC
from tradeengine.services.alert_publisher import alert_publisher

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — falling back to default=%s", name, raw, default)
        return default


# AC7.a — operator-tunable; defaults match the ticket spec.
WINDOW_SECONDS = _env_int("TRADEENGINE_HALT_WINDOW_SECONDS", 300)
COUNT_THRESHOLD = _env_int("TRADEENGINE_HALT_COUNT_THRESHOLD", 10)


class HaltSuspectedDetector:
    """Sliding-window detector + dedup state machine for halt alerts.

    Trigger logic (AC7.a):
      - count of ``source="balance"`` rejections inside the trailing
        ``WINDOW_SECONDS`` window exceeds ``COUNT_THRESHOLD``, OR
      - the entire trailing ``WINDOW_SECONDS`` window contains rejections
        only and the oldest rejection is older than ``WINDOW_SECONDS``
        (i.e. the duration with all-rejected has exceeded the window).

    Dedup (AC7.b/c):
      - while ``halt_suspected`` is active, repeat triggers do not re-emit.
      - the first non-balance-rejected completion (fill / placed / a
        non-balance rejection) resets the state and emits
        ``halt_cleared``.
    """

    def __init__(
        self,
        *,
        publisher=alert_publisher,
        now: Callable[[], datetime] | None = None,
        window_seconds: int | None = None,
        count_threshold: int | None = None,
    ) -> None:
        self._publisher = publisher
        self._now = now or (lambda: datetime.now(UTC))
        self._window_seconds = (
            window_seconds if window_seconds is not None else WINDOW_SECONDS
        )
        self._count_threshold = (
            count_threshold if count_threshold is not None else COUNT_THRESHOLD
        )
        # deque of (timestamp, decision_id) for source=balance rejections only.
        self._balance_rejections: deque[tuple[datetime, str]] = deque()
        self._halt_active: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internals

    def _should_emit(self, now: datetime) -> bool:
        """Decide whether to fire halt_suspected at ``now``.

        The ledger is the full run of consecutive balance rejections since
        the last non-balance completion (a fill / placed / non-balance
        rejection clears it via :meth:`on_completion`). Two triggers:

        - **A (count)**: more than ``count_threshold`` rejections inside
          the trailing ``window_seconds`` slice.
        - **B (duration)**: the run has lasted more than
          ``window_seconds`` end-to-end (oldest-to-newest), regardless
          of count.
        """
        if not self._balance_rejections:
            return False

        # Trigger B is checked against the un-pruned ledger so a slow,
        # continuous all-rejected stream still trips. (Pruning before
        # this check would always make the oldest survivor `>= now -
        # window`, defeating the trigger.)
        oldest = self._balance_rejections[0][0]
        if (now - oldest).total_seconds() > self._window_seconds:
            return True

        # Trigger A: how many rejections fall inside the trailing window?
        cutoff = now - timedelta(seconds=self._window_seconds)
        in_window = sum(1 for ts, _ in self._balance_rejections if ts >= cutoff)
        return in_window > self._count_threshold

    # ------------------------------------------------------------------
    # Public hooks (called from dispatcher emit helpers)

    async def on_rejection(
        self,
        *,
        rejection_source: str | None,
        decision_id: str | None,
    ) -> None:
        """Record one rejection. Triggers halt-suspected if thresholds cross."""
        async with self._lock:
            now = self._now()
            if rejection_source != "balance":
                # A non-balance rejection counts as a non-balance completion
                # — same semantics as a fill from the detector's POV.
                await self._maybe_clear(now)
                return

            self._balance_rejections.append((now, decision_id or ""))

            if self._halt_active or not self._should_emit(now):
                return

            await self._emit_halt_suspected(now)

    async def on_completion(self) -> None:
        """A non-balance-rejected order completed (fill / placed / partial)."""
        async with self._lock:
            await self._maybe_clear(self._now())

    # ------------------------------------------------------------------
    # Emit paths

    async def _emit_halt_suspected(self, now: datetime) -> None:
        # Last-10 reflects the most recent rejections at trigger time.
        # We slice from the un-pruned ledger so the alert payload mirrors
        # the operator's mental model of "what happened just before".
        recent = [d_id for _, d_id in self._balance_rejections if d_id]
        last_ten = recent[-10:]
        payload = {
            "window_seconds": self._window_seconds,
            "count_threshold": self._count_threshold,
            "rejected_count": len(self._balance_rejections),
            "last_10_decision_ids": last_ten,
            "first_rejected_at": self._balance_rejections[0][0]
            .astimezone(UTC)
            .isoformat(),
            "detected_at": now.astimezone(UTC).isoformat(),
        }
        self._halt_active = True
        try:
            await self._publisher.publish(
                alert_name="halt_suspected",
                severity="critical",
                payload=payload,
                timestamp=now,
            )
            logger.warning(
                "halt_suspected emitted: rejected_count=%s window_seconds=%s",
                payload["rejected_count"],
                self._window_seconds,
            )
        except Exception as exc:  # pragma: no cover — publisher is best-effort
            logger.error("halt_suspected emit failed: %s", exc, exc_info=True)

    async def _maybe_clear(self, now: datetime) -> None:
        # A non-balance completion always resets the rejection ledger so we
        # do not double-count rejections that preceded a recovery period.
        self._balance_rejections.clear()
        if not self._halt_active:
            return
        self._halt_active = False
        payload = {
            "cleared_at": now.astimezone(UTC).isoformat(),
        }
        try:
            await self._publisher.publish(
                alert_name="halt_cleared",
                severity="info",
                payload=payload,
                timestamp=now,
            )
            logger.info("halt_cleared emitted")
        except Exception as exc:  # pragma: no cover — best-effort
            logger.error("halt_cleared emit failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Test introspection (kept narrow on purpose)

    @property
    def is_halt_active(self) -> bool:
        return self._halt_active

    @property
    def tracked_rejection_count(self) -> int:
        return len(self._balance_rejections)


halt_suspected_detector = HaltSuspectedDetector()
