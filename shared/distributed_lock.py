"""
Distributed Lock Manager for Petrosa Trading Engine using MongoDB

This module provides distributed locking capabilities to ensure consensus
across multiple pods in the Kubernetes deployment using MongoDB.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import pymongo.errors
from prometheus_client import Counter

from shared.config import Settings
from shared.constants import UTC, get_mongodb_connection_string, redact_uri

logger = logging.getLogger(__name__)


lock_init_failed_total = Counter(
    "tradeengine_lock_init_failed_total",
    "Total MongoDB init failures in the distributed lock manager (boot + lazy reconnect).",
)
lock_acquire_unavailable_total = Counter(
    "tradeengine_lock_acquire_unavailable_total",
    "Total acquire_lock/release_lock calls dropped because MongoDB was unavailable.",
    ["operation"],
)
lock_reconnect_attempts_total = Counter(
    "tradeengine_lock_reconnect_attempts_total",
    "Total lazy-reconnect attempts initiated against MongoDB after a prior failure.",
)
lock_reconnect_success_total = Counter(
    "tradeengine_lock_reconnect_success_total",
    "Total lazy-reconnect attempts that restored MongoDB connectivity.",
)


# Lazy-reconnect cooldown bounds (seconds). The schedule is exponential up to the cap
# so a long Atlas outage doesn't translate into one Mongo handshake per signal.
LOCK_RECONNECT_MIN_BACKOFF_SECONDS = 1.0
LOCK_RECONNECT_MAX_BACKOFF_SECONDS = 30.0


class DistributedLockManager:
    """Manages distributed locks for coordination across pods using MongoDB"""

    def __init__(self) -> None:
        self.pod_id = os.getenv("HOSTNAME", str(uuid.uuid4()))
        self.lock_timeout = int(os.getenv("LOCK_TIMEOUT_SECONDS", "60"))
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
        self.is_leader = False
        self.leader_pod_id: str | None = None
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.lock_cleanup_task: asyncio.Task[None] | None = None
        self.settings = Settings()
        self.mongodb_client: Any = None
        self.mongodb_db: Any = None
        # Lazy-reconnect state. Backoff is bounded so any momentary Mongo
        # unavailability heals without a pod restart (issue #442).
        self._init_lock: asyncio.Lock = asyncio.Lock()
        self._init_backoff_seconds: float = LOCK_RECONNECT_MIN_BACKOFF_SECONDS
        self._init_failure_count: int = 0
        self._last_init_attempt_at: datetime | None = None
        self._last_init_error: str | None = None
        self._alert_emitted_for_current_outage: bool = False

    async def initialize(self) -> None:
        """Initialize distributed lock manager with MongoDB"""
        try:
            # Initialize MongoDB connection. Failure is non-fatal: the manager
            # survives in a degraded state and acquire_lock/release_lock will
            # lazily attempt to reconnect (issue #442 AC3).
            try:
                await self._initialize_mongodb()
            except Exception as init_exc:
                logger.error(
                    "Distributed lock manager booted with MongoDB unavailable "
                    "(will lazy-reconnect): %s",
                    init_exc,
                )

            # Start cleanup task for expired locks
            self.lock_cleanup_task = asyncio.create_task(self._cleanup_expired_locks())

            # Try to become leader (no-op if Mongo not connected yet — heals on reconnect)
            await self._try_become_leader()

            logger.info(f"Distributed lock manager initialized for pod {self.pod_id}")
        except Exception as e:
            logger.error(f"Failed to initialize distributed lock manager: {e}")

    async def close(self) -> None:
        """Close distributed lock manager"""
        try:
            # Stop heartbeat if we're the leader
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                try:
                    await self.heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Stop cleanup task
            if self.lock_cleanup_task:
                self.lock_cleanup_task.cancel()
                try:
                    await self.lock_cleanup_task
                except asyncio.CancelledError:
                    pass

            # Release leadership if we are the leader
            if self.is_leader:
                await self._release_leadership()

            # Close MongoDB connection
            if self.mongodb_client:
                self.mongodb_client.close()

            logger.info("Distributed lock manager closed")
        except Exception as e:
            logger.error(f"Error closing distributed lock manager: {e}")

    async def _initialize_mongodb(self) -> None:
        """Initialize MongoDB connection"""
        try:
            import motor.motor_asyncio

            # Get MongoDB connection string from constants with validation
            from shared.constants import MONGODB_DATABASE, get_mongodb_connection_string

            mongodb_url = self.settings.mongodb_uri or get_mongodb_connection_string()
            database_name = self.settings.mongodb_database or MONGODB_DATABASE

            # Ensure database_name is a string
            if database_name is None:
                raise ValueError("MongoDB database name is required")

            self.mongodb_client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_url)
            self.mongodb_db = self.mongodb_client[str(database_name)]

            # Test connection
            await self.mongodb_client.admin.command("ping")
            logger.info(f"MongoDB connected for distributed locks: {mongodb_url}")

            # Ensure unique index on lock_name for atomic upserts
            await self.mongodb_db.distributed_locks.create_index(
                "lock_name", unique=True
            )
            logger.info("Unique index on distributed_locks.lock_name confirmed")

            # Reset reconnect/backoff state on successful init.
            self._init_backoff_seconds = LOCK_RECONNECT_MIN_BACKOFF_SECONDS
            self._init_failure_count = 0
            self._last_init_error = None
            self._alert_emitted_for_current_outage = False

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB for distributed locks: {e}")
            self.mongodb_client = None
            self.mongodb_db = None
            self._init_failure_count += 1
            self._last_init_error = repr(e)
            lock_init_failed_total.inc()
            raise

    async def _ensure_mongodb_connected(self) -> bool:
        """Best-effort lazy reconnect to MongoDB (issue #442 AC1).

        Returns ``True`` when ``mongodb_db`` is connected after the call (either
        because it was already connected or the reconnect succeeded), ``False``
        otherwise. Honours an exponential backoff between attempts so a long
        Atlas outage doesn't translate into one connection handshake per signal.
        Never raises.
        """
        if self.mongodb_db is not None:
            return True

        async with self._init_lock:
            # Double-check after acquiring the lock — another coroutine may
            # already have reconnected while we were waiting.
            if self.mongodb_db is not None:
                return True

            now = datetime.now(UTC)
            if self._last_init_attempt_at is not None:
                elapsed = (now - self._last_init_attempt_at).total_seconds()
                if elapsed < self._init_backoff_seconds:
                    return False

            self._last_init_attempt_at = now
            lock_reconnect_attempts_total.inc()

            previous_failure_count = self._init_failure_count
            try:
                await self._initialize_mongodb()
            except Exception:
                # _initialize_mongodb already logged + bumped the failure counter.
                # Schedule the next attempt further out so we back off cleanly.
                self._init_backoff_seconds = min(
                    self._init_backoff_seconds * 2.0,
                    LOCK_RECONNECT_MAX_BACKOFF_SECONDS,
                )
                if not self._alert_emitted_for_current_outage:
                    await self._emit_lock_init_failed_alert()
                    self._alert_emitted_for_current_outage = True
                return False

            lock_reconnect_success_total.inc()
            logger.info(
                "Distributed lock manager reconnected to MongoDB after %d failure(s)",
                previous_failure_count,
            )
            return True

    async def _emit_lock_init_failed_alert(self) -> None:
        """Publish a one-shot ``alerts.tradeengine.lock_init_failed`` alert.

        Best-effort: any failure in the alert path is swallowed so it cannot
        break the trading flow further. The lazy import avoids a hard
        ``shared`` → ``tradeengine`` layer dependency at module load time.
        """
        try:
            from tradeengine.services.alert_publisher import alert_publisher

            await alert_publisher.publish(
                alert_name="lock_init_failed",
                severity="high",
                payload={
                    "pod_id": self.pod_id,
                    "init_failure_count": self._init_failure_count,
                    "last_error": self._last_init_error,
                    "backoff_seconds": self._init_backoff_seconds,
                    "reason": (
                        "Distributed lock manager cannot reach MongoDB. "
                        "All order placement is gated through this lock; "
                        "until MongoDB returns, this pod will reject every "
                        "admitted signal at acquire_lock."
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001 - alert path must never raise
            logger.warning(
                "Failed to publish lock_init_failed alert (continuing): %s", exc
            )

    async def acquire_lock(
        self, lock_name: str, timeout_seconds: int | None = None
    ) -> bool:
        """Acquire a distributed lock using MongoDB"""
        # Lazy reconnect (issue #442 AC1): if Mongo is currently unavailable,
        # try to restore the connection before short-circuiting. This is what
        # prevents a transient Mongo blip from permanently halting trading.
        if self.mongodb_db is None and not await self._ensure_mongodb_connected():
            lock_acquire_unavailable_total.labels(operation="acquire").inc()
            logger.warning(
                "lock_acquire_unavailable lock_name=%s pod_id=%s "
                "init_failure_count=%d backoff_seconds=%.1f — MongoDB not "
                "reachable, lock acquisition will fail",
                lock_name,
                self.pod_id,
                self._init_failure_count,
                self._init_backoff_seconds,
            )
            return False

        timeout = timeout_seconds or self.lock_timeout
        expires_at = datetime.now(UTC) + timedelta(seconds=timeout)

        try:
            distributed_locks = self.mongodb_db.distributed_locks

            # Use a more atomic filter to ensure we only acquire if it's expired OR owned by us
            # This prevents race conditions where multiple pods think they have the lock
            filter_query = {
                "lock_name": lock_name,
                "$or": [
                    {"expires_at": {"$lt": datetime.now(UTC)}},
                    {"pod_id": self.pod_id},
                ],
            }

            # Use find_one_and_update for atomic read-and-write with upsert fallback logic
            # Note: upsert in find_one_and_update can create duplicates if no match found,
            # so we use a unique index on lock_name (must be created in initialize)
            try:
                result = await distributed_locks.find_one_and_update(
                    filter_query,
                    {
                        "$set": {
                            "pod_id": self.pod_id,
                            "acquired_at": datetime.now(UTC),
                            "expires_at": expires_at,
                            "updated_at": datetime.now(UTC),
                        }
                    },
                    upsert=True,
                    return_document=True,
                )

                if result:
                    logger.debug(f"Lock '{lock_name}' acquired by pod {self.pod_id}")
                    return True
            except pymongo.errors.DuplicateKeyError:
                # If upsert fails due to DuplicateKeyError, it means another pod just got it
                logger.debug(
                    f"Race condition: Pod {self.pod_id} lost lock '{lock_name}' to another pod"
                )
                return False
            except Exception as e:
                # Log other errors but don't crash
                logger.error(
                    f"Unexpected error in find_one_and_update for lock '{lock_name}': {e}"
                )
                raise

            return False

        except Exception as e:
            logger.error(f"Error acquiring lock '{lock_name}': {e}")
            return False

    async def release_lock(self, lock_name: str) -> bool:
        """Release a distributed lock"""
        if self.mongodb_db is None and not await self._ensure_mongodb_connected():
            lock_acquire_unavailable_total.labels(operation="release").inc()
            logger.warning(
                "lock_acquire_unavailable lock_name=%s pod_id=%s operation=release "
                "— MongoDB not reachable, cannot release lock",
                lock_name,
                self.pod_id,
            )
            return False

        try:
            distributed_locks = self.mongodb_db.distributed_locks
            result = await distributed_locks.delete_one(
                {"lock_name": lock_name, "pod_id": self.pod_id}
            )

            if result.deleted_count > 0:
                logger.debug(f"Lock '{lock_name}' released by pod {self.pod_id}")
                return True
            else:
                logger.debug(
                    f"Lock '{lock_name}' not found or not owned by pod {self.pod_id}"
                )
                return False

        except Exception as e:
            logger.error(f"Error releasing lock '{lock_name}': {e}")
            return False

    async def _try_become_leader(self) -> bool:
        """Try to become the leader pod using MongoDB"""
        if self.mongodb_db is None:
            return False

        try:
            leader_election = self.mongodb_db.leader_election

            # Check if there's already a leader
            current_leader = await leader_election.find_one({"status": "leader"})

            if current_leader:
                current_leader_pod = current_leader["pod_id"]
                last_heartbeat = current_leader["last_heartbeat"]

                # Ensure last_heartbeat is aware (MongoDB might return naive UTC)
                if last_heartbeat and last_heartbeat.tzinfo is None:
                    last_heartbeat = last_heartbeat.replace(tzinfo=UTC)

                # Check if current leader is stale (no heartbeat for 30 seconds)
                if (
                    last_heartbeat
                    and (datetime.now(UTC) - last_heartbeat).total_seconds() < 30
                ):
                    # Current leader is still active
                    self.is_leader = False
                    self.leader_pod_id = current_leader_pod
                    logger.info(
                        f"Pod {self.pod_id} is a follower. Leader: {current_leader_pod}"
                    )
                    return False

            # Try to become leader
            result = await leader_election.update_one(
                {"status": "leader"},
                {
                    "$set": {
                        "pod_id": self.pod_id,
                        "elected_at": datetime.now(UTC),
                        "last_heartbeat": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                    }
                },
                upsert=True,
            )

            # Check if we became the leader
            if result.modified_count > 0 or result.upserted_id:
                self.is_leader = True
                self.leader_pod_id = self.pod_id
                logger.info(f"Pod {self.pod_id} became the leader")

                # Start heartbeat
                self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                return True
            else:
                # Another pod became leader
                current_leader = await leader_election.find_one({"status": "leader"})
                if current_leader:
                    self.is_leader = False
                    self.leader_pod_id = current_leader["pod_id"]
                    logger.info(
                        f"Pod {self.pod_id} is a follower. Leader: "
                        f"{current_leader['pod_id']}"
                    )
                return False

        except Exception as e:
            logger.error(f"Error in leader election: {e}")
            return False

    async def _release_leadership(self) -> None:
        """Release leadership"""
        if self.mongodb_db is None:
            return

        try:
            leader_election = self.mongodb_db.leader_election
            result = await leader_election.delete_one(
                {"pod_id": self.pod_id, "status": "leader"}
            )

            if result.deleted_count > 0:
                self.is_leader = False
                self.leader_pod_id = None
                logger.info(f"Pod {self.pod_id} released leadership")

        except Exception as e:
            logger.error(f"Error releasing leadership: {e}")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats as leader"""
        while self.is_leader:
            try:
                if self.mongodb_db is not None:
                    leader_election = self.mongodb_db.leader_election
                    await leader_election.update_one(
                        {"pod_id": self.pod_id, "status": "leader"},
                        {
                            "$set": {
                                "last_heartbeat": datetime.now(UTC),
                                "updated_at": datetime.now(UTC),
                            }
                        },
                    )

                    logger.debug(f"Leader heartbeat sent by pod {self.pod_id}")

                await asyncio.sleep(self.heartbeat_interval)

            except Exception as e:
                logger.error(f"Error sending heartbeat: {e}")
                await asyncio.sleep(self.heartbeat_interval)

    async def _cleanup_expired_locks(self) -> None:
        """Periodically cleanup expired locks"""
        while True:
            try:
                if self.mongodb_db is not None:
                    distributed_locks = self.mongodb_db.distributed_locks
                    result = await distributed_locks.delete_many(
                        {"expires_at": {"$lt": datetime.now(UTC)}}
                    )

                    if result.deleted_count > 0:
                        logger.debug(f"Cleaned up {result.deleted_count} expired locks")

                await asyncio.sleep(60)  # Cleanup every minute

            except Exception as e:
                logger.error(f"Error cleaning up expired locks: {e}")
                await asyncio.sleep(60)

    async def get_leader_info(self) -> dict[str, Any]:
        """Get current leader information"""
        if self.mongodb_db is None:
            return {"status": "unknown", "error": "MongoDB not available"}

        try:
            leader_election = self.mongodb_db.leader_election
            leader_doc = await leader_election.find_one({"status": "leader"})

            if leader_doc:
                return {
                    "leader_pod_id": leader_doc["pod_id"],
                    "status": leader_doc["status"],
                    "last_heartbeat": (
                        leader_doc["last_heartbeat"].isoformat()
                        if leader_doc["last_heartbeat"]
                        else None
                    ),
                    "is_current_leader": leader_doc["pod_id"] == self.pod_id,
                    "current_pod_id": self.pod_id,
                    "elected_at": (
                        leader_doc["elected_at"].isoformat()
                        if leader_doc["elected_at"]
                        else None
                    ),
                }
            else:
                return {
                    "leader_pod_id": None,
                    "status": "no_leader",
                    "last_heartbeat": None,
                    "is_current_leader": False,
                    "current_pod_id": self.pod_id,
                    "elected_at": None,
                }

        except Exception as e:
            logger.error(f"Error getting leader info: {e}")
            return {"status": "error", "error": str(e)}

    async def health_check(self) -> dict[str, Any]:
        """Health check for distributed lock manager.

        Per issue #442 AC2: when MongoDB is unavailable, the lock manager is
        a first-class unhealthy signal — every admitted signal will be
        rejected at ``acquire_lock`` until reconnect succeeds.
        """
        mongodb_connected = self.mongodb_db is not None
        status = "healthy" if mongodb_connected else "unhealthy"

        leader_info: dict[str, Any]
        if mongodb_connected:
            leader_info = await self.get_leader_info()
        else:
            leader_info = {
                "status": "unknown",
                "error": "MongoDB not available",
            }

        return {
            "status": status,
            "pod_id": self.pod_id,
            "is_leader": self.is_leader,
            "leader_info": leader_info,
            "mongodb_connected": mongodb_connected,
            "mongodb_uri": redact_uri(
                self.settings.mongodb_uri or get_mongodb_connection_string()
            ),
            "lock_timeout": self.lock_timeout,
            "heartbeat_interval": self.heartbeat_interval,
            "reconnect": {
                "init_failure_count": self._init_failure_count,
                "last_init_attempt_at": (
                    self._last_init_attempt_at.isoformat()
                    if self._last_init_attempt_at is not None
                    else None
                ),
                "backoff_seconds": self._init_backoff_seconds,
                "last_error": self._last_init_error,
            },
        }

    async def execute_with_lock(
        self, lock_name: str, operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        """Execute an operation with a distributed lock"""
        acquired = await self.acquire_lock(lock_name)
        if not acquired:
            raise Exception(f"Failed to acquire lock '{lock_name}'")

        try:
            return await operation(*args, **kwargs)
        finally:
            await self.release_lock(lock_name)


# Global distributed lock manager instance
distributed_lock_manager = DistributedLockManager()
