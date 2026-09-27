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


class LockState(StrEnum):
    ACQUIRED = "acquired"
    HELD = "held"
    UNAVAILABLE = "unavailable"


class LockUnavailableError(RuntimeError):
    """The lease service could not make a safe lock decision."""


class LockHeldError(RuntimeError):
    """Another owner holds the lease."""


class LeaseClient:
    """Small, single-attempt client for the data-manager lease API."""

    def __init__(self, base_url: str | None = None, timeout: float = 1.5) -> None:
        self.base_url = base_url or os.getenv(
            "DATA_MANAGER_URL", "http://petrosa-data-manager:8000"
        )
        self.timeout = timeout
        self._client = BaseDataManagerClient(
            base_url=self.base_url, timeout=timeout, max_retries=1
        )

    async def _request(
        self, op: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return await self._client.request(method, path, json=body)
        finally:
            lease_request_seconds.labels(op=op).observe(time.perf_counter() - started)

    async def acquire(self, name: str, owner: str, ttl_s: int) -> dict[str, Any]:
        return await self._request(
            "acquire",
            "POST",
            f"/api/v1/locks/{name}/acquire",
            {"owner": owner, "ttl_s": ttl_s},
        )

    async def renew(self, name: str, owner: str, ttl_s: int) -> dict[str, Any]:
        return await self._request(
            "renew",
            "POST",
            f"/api/v1/locks/{name}/renew",
            {"owner": owner, "ttl_s": ttl_s},
        )

    async def release(self, name: str, owner: str) -> dict[str, Any]:
        return await self._request(
            "release", "POST", f"/api/v1/locks/{name}/release", {"owner": owner}
        )

    async def get(self, name: str) -> dict[str, Any] | None:
        try:
            return await self._request("get", "GET", f"/api/v1/locks/{name}")
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
        self.is_leader = False
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
        self.settings = Settings()
        self.lease_client = LeaseClient(timeout=self.lease_timeout)

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
            lease_unavailable_total.labels(op="acquire").inc()
            logger.warning("Lease acquire unavailable for %s: %s", lock_name, exc)
            return LockState.UNAVAILABLE
        return LockState.ACQUIRED if response.get("acquired") else LockState.HELD

    async def release_lock(self, lock_name: str) -> bool:
        try:
            response = await self.lease_client.release(lock_name, self.pod_id)
            return bool(response.get("released"))
        except Exception as exc:
            lease_unavailable_total.labels(op="release").inc()
            logger.warning("Lease release failed for %s: %s", lock_name, exc)
            return False

    async def _try_become_leader(self) -> bool:
        try:
            response = await self.lease_client.acquire(
                "tradeengine-leader", self.pod_id, 30
            )
        except Exception as exc:
            lease_unavailable_total.labels(op="acquire").inc()
            logger.warning("Leader lease unavailable: %s", exc)
            self.is_leader = False
            return False
        self.is_leader = bool(response.get("acquired"))
        self.leader_pod_id = self.pod_id if self.is_leader else response.get("owner")
        if self.is_leader:
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self.is_leader

    async def _release_leadership(self) -> None:
        await self.release_lock("tradeengine-leader")
        self.is_leader = False
        self.leader_pod_id = None

    async def _heartbeat_loop(self) -> None:
        while self.is_leader:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                response = await self.lease_client.renew(
                    "tradeengine-leader", self.pod_id, 30
                )
                if not response.get("renewed"):
                    self.is_leader = False
                    self.leader_pod_id = response.get("owner")
            except Exception as exc:
                lease_unavailable_total.labels(op="renew").inc()
                logger.warning("Leader lease renewal failed: %s", exc)
                self.is_leader = False

    async def _leader_info_refresh_loop(self) -> None:
        while True:
            try:
                self._cached_leader_info = await self.get_leader_info()
            except Exception as exc:
                logger.debug("Leader info refresh failed: %s", exc)
            await asyncio.sleep(self.heartbeat_interval)

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
            "leader_info": self._cached_leader_info,
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
