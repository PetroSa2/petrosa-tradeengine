"""Lease-backed distributed locks for TradeEngine."""

import asyncio
import logging
import os
import time
import uuid
from enum import StrEnum
from typing import Any

from prometheus_client import Counter, Histogram

from shared.config import Settings
from tradeengine.services.data_manager_client import APIError, BaseDataManagerClient

logger = logging.getLogger(__name__)

LOCK_RECONNECT_MIN_BACKOFF_SECONDS = 1.0
LOCK_RECONNECT_MAX_BACKOFF_SECONDS = 30.0

lease_request_seconds = Histogram(
    "tradeengine_lease_request_seconds", "Lease API request latency", ["op"]
)
lease_unavailable_total = Counter(
    "tradeengine_lease_unavailable_total", "Lease API failures", ["op"]
)
lease_outcomes_total = Counter(
    "tradeengine_lease_outcomes_total",
    "Lease API outcomes by operation and result.",
    ["op", "outcome"],
)
lease_call_total = Counter(
    "tradeengine_lease_call_total",
    "Lease API calls by operation and result.",
    ["op", "result"],
)

_lease_log_state: dict[tuple[str, str], tuple[float, int]] = {}
_LEASE_LOG_INTERVAL = 60.0


def _rate_limited_lease_warning(op: str, exc: BaseException) -> None:
    """Emit one warning per operation/error class and summarize suppressed calls."""
    now = time.monotonic()
    key = (op, type(exc).__name__)
    last, suppressed = _lease_log_state.get(key, (0.0, 0))
    if now - last < _LEASE_LOG_INTERVAL:
        _lease_log_state[key] = (last, suppressed + 1)
        return
    logger.warning(
        "Lease %s failed: %s suppressed=%d", op, _format_exception(exc), suppressed
    )
    _lease_log_state[key] = (now, 0)


DEFAULT_LEASE_TIMEOUT_SECONDS = 5.0
LEASE_MAX_RETRIES = 2
LEASE_RETRY_BACKOFF_BASE_SECONDS = 0.5
LEASE_RETRY_JITTER_MAX_SECONDS = 0.25


def lease_retry_budget_seconds(
    timeout: float, max_retries: int = LEASE_MAX_RETRIES
) -> float:
    """Return the worst-case request and backoff budget for a lease call."""
    attempts = max(1, int(max_retries))
    request_budget = timeout * attempts
    backoff_budget = sum(
        min(
            LEASE_RETRY_BACKOFF_BASE_SECONDS * (2**attempt),
            8.0,
        )
        + LEASE_RETRY_JITTER_MAX_SECONDS
        for attempt in range(attempts - 1)
    )
    return request_budget + backoff_budget


def validate_lease_ttl_budget(
    ttl_seconds: int, timeout: float, max_retries: int = LEASE_MAX_RETRIES
) -> None:
    """Ensure retries can finish before the lease expires."""
    if lease_retry_budget_seconds(timeout, max_retries) >= ttl_seconds:
        raise ValueError(
            "lease retry budget must stay below the TTL: "
            f"{lease_retry_budget_seconds(timeout, max_retries):.2f}s >= {ttl_seconds}s"
        )


class LockState(StrEnum):
    ACQUIRED = "acquired"
    HELD = "held"
    UNAVAILABLE = "unavailable"


class LockUnavailableError(RuntimeError):
    """The lease service could not make a safe lock decision."""


class LockHeldError(RuntimeError):
    """Another owner holds the lease."""


class LeaseClient:
    """Client for the data-manager lease API with bounded transient retries."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_LEASE_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "DATA_MANAGER_URL", "http://petrosa-data-manager:8000"
        )
        self.timeout = timeout
        self._client = BaseDataManagerClient(
            base_url=self.base_url, timeout=timeout, max_retries=LEASE_MAX_RETRIES
        )

    async def _request(
        self, op: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._client.request(method, path, json=body)
            lease_call_total.labels(op=op, result="success").inc()
            return response
        except Exception as exc:
            lease_call_total.labels(
                op=op, result="timeout" if _is_timeout(exc) else "error"
            ).inc()
            raise
        finally:
            lease_request_seconds.labels(op=op).observe(time.perf_counter() - started)

    async def acquire(self, name: str, owner: str, ttl_s: int) -> dict[str, Any]:
        validate_lease_ttl_budget(ttl_s, self.timeout, LEASE_MAX_RETRIES)
        return await self._request(
            "acquire",
            "POST",
            f"/api/v1/leases/{name}/acquire",
            {"owner": owner, "ttl_seconds": ttl_s},
        )

    async def renew(self, name: str, owner: str, ttl_s: int) -> dict[str, Any]:
        validate_lease_ttl_budget(ttl_s, self.timeout, LEASE_MAX_RETRIES)
        return await self._request(
            "renew",
            "POST",
            f"/api/v1/leases/{name}/renew",
            {"owner": owner, "ttl_seconds": ttl_s},
        )

    async def release(self, name: str, owner: str) -> dict[str, Any]:
        return await self._request(
            "release", "POST", f"/api/v1/leases/{name}/release", {"owner": owner}
        )

    async def get(self, name: str) -> dict[str, Any] | None:
        try:
            return await self._request("get", "GET", f"/api/v1/leases/{name}")
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def close(self) -> None:
        await self._client.close()


class DistributedLockManager:
    """Preserve the historical manager API while using data-manager leases."""

    def __init__(self) -> None:
        self.pod_id = os.getenv("HOSTNAME", str(uuid.uuid4()))
        self.lock_timeout = int(os.getenv("LOCK_TIMEOUT_SECONDS", "60"))
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
        self.lease_timeout = float(os.getenv("TE_LEASE_TIMEOUT_S", "1.5"))
        self.settings = Settings()
        self._is_leader = False
        self.lease_valid_until = 0.0
        self.lease_safety_margin = float(
            os.getenv(
                "TE_LEASE_SAFETY_MARGIN_S", str(self.settings.te_lease_safety_margin_s)
            )
        )
        self.leader_pod_id: str | None = None
        self.heartbeat_task: asyncio.Task[None] | None = None
        self._leader_info_refresh_task: asyncio.Task[None] | None = None
        self._cached_leader_info: dict[str, Any] = {
            "leader_pod_id": None,
            "status": "unknown",
            "last_heartbeat": None,
            "is_current_leader": False,
            "current_pod_id": self.pod_id,
            "elected_at": None,
        }
        self.lease_client = LeaseClient(timeout=self.lease_timeout)

    @property
    def is_leader(self) -> bool:
        return self._is_leader and time.monotonic() < self.lease_valid_until

    @is_leader.setter
    def is_leader(self, value: bool) -> None:
        self._is_leader = value
        if value and self.lease_valid_until <= time.monotonic():
            # Preserve test/manual callers that set the legacy flag directly;
            # real acquire and renew paths always replace this with a deadline.
            self.lease_valid_until = float("inf")
        if not value:
            self.lease_valid_until = 0.0

    async def initialize(self) -> None:
        await self._try_become_leader()
        self._leader_info_refresh_task = asyncio.create_task(
            self._leader_info_refresh_loop()
        )
        logger.info("Distributed lease manager initialized for pod %s", self.pod_id)

    async def close(self) -> None:
        for task in (self.heartbeat_task, self._leader_info_refresh_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.is_leader:
            await self._release_leadership()
        await self.lease_client.close()

    async def acquire_lock(
        self, lock_name: str, timeout_seconds: int | None = None
    ) -> LockState:
        ttl = timeout_seconds or self.lock_timeout
        try:
            response = await self.lease_client.acquire(lock_name, self.pod_id, ttl)
        except Exception as exc:
            lease_outcomes_total.labels(
                op="acquire", outcome="timeout" if _is_timeout(exc) else "error"
            ).inc()
            lease_unavailable_total.labels(op="acquire").inc()
            _rate_limited_lease_warning("acquire", exc)
            return LockState.UNAVAILABLE
        lease_outcomes_total.labels(
            op="acquire", outcome="acquired" if response.get("acquired") else "error"
        ).inc()
        return LockState.ACQUIRED if response.get("acquired") else LockState.HELD

    async def release_lock(self, lock_name: str) -> bool:
        try:
            response = await self.lease_client.release(lock_name, self.pod_id)
            return bool(response.get("released"))
        except Exception as exc:
            lease_outcomes_total.labels(
                op="release", outcome="timeout" if _is_timeout(exc) else "error"
            ).inc()
            lease_unavailable_total.labels(op="release").inc()
            _rate_limited_lease_warning("release", exc)
            return False

    async def _try_become_leader(self) -> bool:
        request_started = time.monotonic()
        ttl = 30
        try:
            response = await self.lease_client.acquire(
                "tradeengine-leader", self.pod_id, ttl
            )
        except Exception as exc:
            lease_outcomes_total.labels(
                op="acquire", outcome="timeout" if _is_timeout(exc) else "error"
            ).inc()
            lease_unavailable_total.labels(op="acquire").inc()
            _rate_limited_lease_warning("acquire", exc)
            self.is_leader = False
            return False
        self.is_leader = bool(response.get("acquired"))
        lease_outcomes_total.labels(
            op="acquire", outcome="acquired" if self.is_leader else "error"
        ).inc()
        self.leader_pod_id = self.pod_id if self.is_leader else response.get("owner")
        if self.is_leader:
            self.lease_valid_until = request_started + ttl - self.lease_safety_margin
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self.is_leader

    async def _release_leadership(self) -> None:
        await self.release_lock("tradeengine-leader")
        self.is_leader = False
        self.leader_pod_id = None

    async def _heartbeat_loop(self) -> None:
        while self.is_leader:
            await asyncio.sleep(self.heartbeat_interval)
            request_started = time.monotonic()
            try:
                response = await self.lease_client.renew(
                    "tradeengine-leader", self.pod_id, 30
                )
                if not response.get("renewed"):
                    lease_outcomes_total.labels(op="renew", outcome="error").inc()
                    self.is_leader = False
                    self.leader_pod_id = response.get("owner")
            except Exception as exc:
                lease_outcomes_total.labels(
                    op="renew", outcome="timeout" if _is_timeout(exc) else "error"
                ).inc()
                lease_unavailable_total.labels(op="renew").inc()
                _rate_limited_lease_warning("renew", exc)
                # Keep leadership during a transient failure; the deadline is
                # the authoritative validity boundary and the loop retries.
                if self.lease_valid_until == float("inf"):
                    self.lease_valid_until = time.monotonic()
            else:
                if response.get("renewed"):
                    lease_outcomes_total.labels(op="renew", outcome="renewed").inc()
                    self.lease_valid_until = (
                        request_started + 30 - self.lease_safety_margin
                    )

    async def _leader_info_refresh_loop(self) -> None:
        while True:
            await self._refresh_leadership_once()
            await asyncio.sleep(self.heartbeat_interval)

    async def _refresh_leadership_once(self) -> None:
        # A single failed renewal (e.g. a data-manager restart) drops
        # leadership; without this the pod never re-acquires it.
        if not self.is_leader:
            await self._try_become_leader()
        try:
            self._cached_leader_info = await self.get_leader_info()
        except Exception as exc:
            logger.debug("Leader info refresh failed: %s", exc)

    async def get_leader_info(self) -> dict[str, Any]:
        try:
            leader = await self.lease_client.get("tradeengine-leader")
        except Exception as exc:
            lease_unavailable_total.labels(op="get").inc()
            return {
                "status": "unavailable",
                "error": str(exc),
                "current_pod_id": self.pod_id,
            }
        if not leader:
            return {"status": "no_leader", "current_pod_id": self.pod_id}
        owner = leader.get("owner")
        return {
            "leader_pod_id": owner,
            "status": "leader",
            "last_heartbeat": None,
            "is_current_leader": owner == self.pod_id,
            "current_pod_id": self.pod_id,
            "elected_at": None,
            "expires_at": leader.get("expires_at"),
        }

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "pod_id": self.pod_id,
            "is_leader": self.is_leader,
            "leader_info": getattr(
                self,
                "_cached_leader_info",
                {"status": "unknown", "current_pod_id": self.pod_id},
            ),
            "lease_available": True,
            "lock_timeout": self.lock_timeout,
            "heartbeat_interval": self.heartbeat_interval,
        }

    async def execute_with_lock(
        self, lock_name: str, operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        state = await self.acquire_lock(lock_name)
        if state is LockState.UNAVAILABLE:
            raise LockUnavailableError(f"Lease unavailable for '{lock_name}'")
        if state is LockState.HELD:
            raise LockHeldError(f"Lock held by another owner: '{lock_name}'")
        try:
            return await operation(*args, **kwargs)
        finally:
            await self.release_lock(lock_name)


distributed_lock_manager = DistributedLockManager()


def _is_timeout(exc: BaseException) -> bool:
    return "timeout" in type(exc).__name__.lower() or "timeout" in repr(exc).lower()


def _format_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc!r}"
