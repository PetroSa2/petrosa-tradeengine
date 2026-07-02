"""Open/close thrash circuit-breaker (#481 AC5).

The 2026-06-18 testnet diagnosis surfaced a sustained position
open/close/reopen oscillation on BNBUSDT and LINKUSDT: fixed-size
reduceOnly closes cycled ~10-20s apart with no price-driven trigger,
driven by ghost strategy-tracker SHORTs. The #480 reconciler evicts the
ghost rows and #481 AC3 adds an emission-time guard against closing a
position the exchange no longer holds. This module is the AC5 fail-safe:
even if a new ghost-generation path slips past both, a symbol cannot be
churned open/closed indefinitely without a CIO-decision audit trail.

Rule
----
No more than ``max_cycles`` (default 2) close emissions on the same symbol
within ``window_minutes`` (default 10) that lack a CIO-decision audit
trail. When the threshold is crossed the breaker "opens" for that symbol
and :meth:`should_block` returns ``True`` for subsequent un-audited closes
until the sliding window clears.

Audited closes (``cio_audited=True`` — e.g. a close that traces to a
petrosa-cio decision) never count toward the threshold and are never
blocked. This keeps legitimate, reasoned exits flowing while starving the
un-reasoned thrash loop.

The breaker is intentionally in-memory and per-process: it is a
last-resort safety valve, not a distributed rate limiter. A pod restart
resets it, which is acceptable — the #480 reconciler and #481 AC3 guard
are the durable fixes; AC5 only bounds the blast radius.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from shared.constants import UTC

_DEFAULT_MAX_CYCLES = 2
_DEFAULT_WINDOW_MINUTES = 10


class ThrashCircuitBreaker:
    """Per-symbol sliding-window guard against open/close thrash (#481 AC5)."""

    def __init__(
        self,
        max_cycles: int = _DEFAULT_MAX_CYCLES,
        window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    ) -> None:
        self._max_cycles = max(1, int(max_cycles))
        self._window = timedelta(minutes=max(1, int(window_minutes)))
        # symbol -> list of (timestamp, cio_audited) for recent close emissions
        self._events: dict[str, list[tuple[datetime, bool]]] = {}
        self._lock = threading.Lock()

    @property
    def max_cycles(self) -> int:
        return self._max_cycles

    @property
    def window_minutes(self) -> int:
        return int(self._window.total_seconds() // 60)

    def _prune(self, symbol: str, now: datetime) -> list[tuple[datetime, bool]]:
        """Drop events outside the window and return the surviving list."""
        cutoff = now - self._window
        events = [e for e in self._events.get(symbol, []) if e[0] >= cutoff]
        if events:
            self._events[symbol] = events
        else:
            self._events.pop(symbol, None)
        return events

    def should_block(self, symbol: str, now: datetime | None = None) -> bool:
        """True when the un-audited close count in the window is at/over the cap.

        Does not record anything — call :meth:`record_close` for emissions that
        are allowed through.
        """
        now = now or datetime.now(UTC)
        with self._lock:
            events = self._prune(symbol, now)
            unaudited = sum(1 for _, audited in events if not audited)
            return unaudited >= self._max_cycles

    def record_close(
        self,
        symbol: str,
        *,
        cio_audited: bool,
        now: datetime | None = None,
    ) -> None:
        """Record an allowed close emission so it counts toward the window."""
        now = now or datetime.now(UTC)
        with self._lock:
            self._prune(symbol, now)
            self._events.setdefault(symbol, []).append((now, bool(cio_audited)))

    def reset(self, symbol: str | None = None) -> None:
        """Clear tracked events for one symbol (or all)."""
        with self._lock:
            if symbol is None:
                self._events.clear()
            else:
                self._events.pop(symbol, None)
