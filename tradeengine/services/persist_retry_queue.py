"""
Failed-write reconciliation/retry path for position persistence.

Provides a bounded in-memory queue that re-attempts position writes that
failed after HTTP-level retries.  Persistent writes require an external
durable store (NATS JetStream or a pending_writes table) — see #448 note.
Closes #448 Task 1.6.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 60.0
_MAX_QUEUE_SIZE = 500


@dataclass
class PendingWrite:
    """A single failed position write waiting to be retried."""

    operation: str
    data: dict[str, Any]
    symbol: str
    position_id: str
    attempts: int = 0
    enqueued_at: datetime = field(default_factory=datetime.utcnow)
    last_error: str = ""


# Type alias for the async callable the drain loop invokes
_WriteFn = Callable[..., Coroutine[Any, Any, Any]]


class PersistRetryQueue:
    """
    Bounded in-memory queue of failed position-persist writes.

    Callers enqueue writes that failed after the HTTP retry budget via
    :meth:`enqueue`.  A background drain task periodically re-attempts
    each pending write; writes that keep failing are surfaced via
    :data:`never_persisted` so the reconciler can detect them as a new
    "never-persisted" divergence category.
    """

    def __init__(
        self,
        *,
        max_size: int = _MAX_QUEUE_SIZE,
        max_drain_attempts: int = 5,
        drain_interval: float = 30.0,
    ) -> None:
        self._queue: asyncio.Queue[PendingWrite] = asyncio.Queue(maxsize=max_size)
        self._max_drain_attempts = max_drain_attempts
        self._drain_interval = drain_interval
        self._drain_task: asyncio.Task[None] | None = None
        self._write_fns: dict[str, _WriteFn] = {}
        # Position IDs that permanently failed — fed to the reconciler
        self.never_persisted: set[str] = set()

    def register(self, operation: str, fn: _WriteFn) -> None:
        """Register the async callable that handles *operation* retries."""
        self._write_fns[operation] = fn

    def enqueue(self, pw: PendingWrite) -> bool:
        """
        Add *pw* to the queue.

        Returns False (with an alert-worthy log) if the queue is full;
        the caller should still increment the failed counter and publish
        the alert regardless.
        """
        try:
            self._queue.put_nowait(pw)
            logger.warning(
                "Enqueued failed %s for %s (position_id=%s) — queue depth %d",
                pw.operation,
                pw.symbol,
                pw.position_id,
                self._queue.qsize(),
            )
            return True
        except asyncio.QueueFull:
            logger.error(
                "PersistRetryQueue full (%d items); dropping %s for %s — "
                "position %s will not be retried automatically",
                _MAX_QUEUE_SIZE,
                pw.operation,
                pw.symbol,
                pw.position_id,
            )
            if pw.position_id:
                self.never_persisted.add(pw.position_id)
            return False

    async def _try_one(self, pw: PendingWrite) -> bool:
        """Attempt one retry of *pw*; return True on success."""
        fn = self._write_fns.get(pw.operation)
        if fn is None:
            logger.error(
                "No handler registered for operation %r — dropping", pw.operation
            )
            return False
        try:
            result = await fn(**pw.data)
            ok = getattr(result, "ok", bool(result))
            return bool(ok)
        except Exception as exc:
            pw.last_error = str(exc)
            return False

    async def _drain_loop(self) -> None:
        """Background task: drain the queue with back-off."""
        while True:
            await asyncio.sleep(self._drain_interval)
            retry_list: list[PendingWrite] = []
            while not self._queue.empty():
                try:
                    retry_list.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            for pw in retry_list:
                pw.attempts += 1
                backoff = min(
                    _BACKOFF_BASE**pw.attempts + random.uniform(0, 1),
                    _BACKOFF_CAP,
                )
                await asyncio.sleep(backoff)
                success = await self._try_one(pw)
                if success:
                    logger.info(
                        "Retry succeeded for %s %s after %d attempts",
                        pw.operation,
                        pw.position_id,
                        pw.attempts,
                    )
                    if pw.position_id:
                        self.never_persisted.discard(pw.position_id)
                elif pw.attempts >= self._max_drain_attempts:
                    logger.error(
                        "Permanently failed %s for position %s after %d attempts — "
                        "surfaced as never-persisted divergence",
                        pw.operation,
                        pw.position_id,
                        pw.attempts,
                    )
                    if pw.position_id:
                        self.never_persisted.add(pw.position_id)
                else:
                    # Re-enqueue for next drain pass
                    try:
                        self._queue.put_nowait(pw)
                    except asyncio.QueueFull:
                        self.never_persisted.add(pw.position_id)

    def start(self) -> None:
        """Start the background drain task (call from the app event loop)."""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.ensure_future(self._drain_loop())
            logger.info("PersistRetryQueue drain task started")

    def stop(self) -> None:
        """Cancel the drain task gracefully."""
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            logger.info("PersistRetryQueue drain task stopped")

    @property
    def depth(self) -> int:
        return self._queue.qsize()


# Module-level singleton — wired up in api.py startup
persist_retry_queue = PersistRetryQueue()
