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

from shared.config import Settings
from shared.constants import get_mongodb_connection_string

logger = logging.getLogger(__name__)


class DistributedLockManager:
    """Manages distributed locks for coordination across pods using MongoDB"""

    def __init__(self) -> None:
        self.pod_id = os.getenv("HOSTNAME", str(uuid.uuid4()))
        self.lock_timeout = int(os.getenv("LOCK_TIMEOUT_SECONDS", "60"))
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
        self.is_leader = False
        self.leader_pod_id: str | None = None
        self.heartbeat_task: asyncio.Task | None = None
        self.lock_cleanup_task: asyncio.Task | None = None
        self.settings = Settings()
        self.mongodb_client: Any = None
        self.mongodb_db: Any = None

    async def initialize(self) -> None:
        """Initialize distributed lock manager with MongoDB"""
        try:
            # Initialize MongoDB connection
            await self._initialize_mongodb()

            # Start cleanup task for expired locks
            self.lock_cleanup_task = asyncio.create_task(self._cleanup_expired_locks())

            # Try to become leader
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

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB for distributed locks: {e}")
            self.mongodb_client = None
            self.mongodb_db = None
            raise

    async def acquire_lock(
        self, lock_name: str, timeout_seconds: int | None = None
    ) -> bool:
        """Acquire a distributed lock using MongoDB"""
        if self.mongodb_db is None:
            logger.warning("MongoDB not available, lock acquisition will fail")
            return False

        timeout = timeout_seconds or self.lock_timeout
        expires_at = datetime.utcnow() + timedelta(seconds=timeout)

        try:
            distributed_locks = self.mongodb_db.distributed_locks

            # Try to acquire lock using MongoDB's atomic operations
            result = await distributed_locks.update_one(
                {"lock_name": lock_name},
                {
                    "$set": {
                        "pod_id": self.pod_id,
                        "acquired_at": datetime.utcnow(),
                        "expires_at": expires_at,
                        "updated_at": datetime.utcnow(),
                    }
                },
                upsert=True,
            )

            # Check if we got the lock (either inserted new or updated existing expired lock)
            if result.modified_count > 0 or result.upserted_id:
                logger.debug(f"Lock '{lock_name}' acquired by pod {self.pod_id}")
                return True
            else:
                # Check if the lock is still held by us
                lock_doc = await distributed_locks.find_one({"lock_name": lock_name})
                if lock_doc and lock_doc["pod_id"] == self.pod_id:
                    logger.debug(
                        f"Lock '{lock_name}' already held by pod {self.pod_id}"
                    )
                    return True
                else:
                    logger.debug(
                        f"Failed to acquire lock '{lock_name}' for pod "
                        f"{self.pod_id}"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error acquiring lock '{lock_name}': {e}")
            return False

    async def release_lock(self, lock_name: str) -> bool:
        """Release a distributed lock"""
        if self.mongodb_db is None:
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
                    f"Lock '{lock_name}' not found or not owned by pod "
                    f"{self.pod_id}"
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

                # Check if current leader is stale (no heartbeat for 30 seconds)
                if (
                    last_heartbeat
                    and (datetime.utcnow() - last_heartbeat).total_seconds() < 30
                ):
                    # Current leader is still active
                    self.is_leader = False
                    self.leader_pod_id = current_leader_pod
                    logger.info(
                        f"Pod {self.pod_id} is a follower. Leader: "
                        f"{current_leader_pod}"
                    )
                    return False

            # Try to become leader
            result = await leader_election.update_one(
                {"status": "leader"},
                {
                    "$set": {
                        "pod_id": self.pod_id,
                        "elected_at": datetime.utcnow(),
                        "last_heartbeat": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
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
                                "last_heartbeat": datetime.utcnow(),
                                "updated_at": datetime.utcnow(),
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
                        {"expires_at": {"$lt": datetime.utcnow()}}
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
        """Health check for distributed lock manager"""
        leader_info = await self.get_leader_info()

        return {
            "status": "healthy",
            "pod_id": self.pod_id,
            "is_leader": self.is_leader,
            "leader_info": leader_info,
            "mongodb_connected": self.mongodb_db is not None,
            "mongodb_uri": (
                self.settings.mongodb_uri
                if self.settings.mongodb_uri
                else get_mongodb_connection_string()
            ),
            "lock_timeout": self.lock_timeout,
            "heartbeat_interval": self.heartbeat_interval,
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
