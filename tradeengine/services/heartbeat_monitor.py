"""
Heartbeat Monitor Service for TradeEngine.
Monitors CIO heartbeat and manages RESTRICTED_MODE fail-safe.
"""

import asyncio
import json
import logging
import time
from typing import Any

import nats
import nats.aio.client
from pydantic import BaseModel, Field

from tradeengine.defaults import FAIL_SAFE_PARAMETERS
from tradeengine.metrics import (
    last_heartbeat_received_timestamp,
    restricted_mode_status,
)

logger = logging.getLogger(__name__)


class HeartbeatMessage(BaseModel):
    """Standardized heartbeat message model."""

    service: str
    timestamp: float = Field(default_factory=time.time)
    version: str = "1.0.0"
    status: str = "healthy"

    def to_json(self) -> str:
        """Compatibility helper for Pydantic v1/v2."""
        if hasattr(self, "model_dump_json"):
            return self.model_dump_json()
        return self.json()


#: MongoDB collection + document id used to persist restricted-mode state
#: across process restarts (tradeengine#533 / H5 of petrosa_k8s#977).
HEARTBEAT_STATE_COLLECTION = "heartbeat_state"
HEARTBEAT_STATE_DOC_ID = "tradeengine"


def _resolve_persist_enabled() -> bool:
    """Resolve whether restricted-mode persistence is enabled.

    Feature flag ``te_heartbeat_persist_enabled`` (string tri-state, mirroring
    ``te_exchange_truth_store_enabled``): ``"off"`` keeps the legacy in-memory
    behavior; any other value (``"on"``) enables durable persistence + restore.
    Reads from the pydantic ``settings`` singleton, falling back to the env var
    directly so the monitor still resolves the flag if settings import fails.
    """
    try:
        from shared.config import settings

        value = str(getattr(settings, "te_heartbeat_persist_enabled", "off"))
    except Exception:
        import os

        value = os.getenv("TE_HEARTBEAT_PERSIST_ENABLED", "off")
    return value.strip().lower() not in ("", "off", "false", "0")


class HeartbeatMonitor:
    """Monitors ecosystem heartbeats and manages fail-safe modes."""

    def __init__(
        self,
        nats_url: str,
        subject: str = "cio.heartbeat",
        timeout: float | None = None,
        recovery_threshold: int | None = None,
        persist_enabled: bool | None = None,
        state_db: Any = None,
    ):
        self.nats_url = nats_url
        self.subject = subject
        self.timeout = timeout or FAIL_SAFE_PARAMETERS["heartbeat_timeout_seconds"]
        self.recovery_threshold = (
            recovery_threshold or FAIL_SAFE_PARAMETERS["recovery_threshold"]
        )

        self.nats_client: nats.aio.client.Client | None = None
        self.last_heartbeat_time: float = 0
        self.consecutive_heartbeats: int = 0
        self.restricted_mode: bool = False
        self.is_running: bool = False
        self._monitor_task: asyncio.Task[Any] | None = None

        # tradeengine#533 (H5 of #977): persist restricted_mode across restart.
        # When persistence is enabled, restart restores the last-known state and
        # an unverifiable state fails *closed* (restricted). When disabled the
        # monitor keeps the legacy in-memory-only behavior for backwards compat.
        if persist_enabled is None:
            persist_enabled = _resolve_persist_enabled()
        self.persist_enabled: bool = persist_enabled
        #: Optional motor AsyncIOMotorDatabase handle. Injected by the dispatcher
        #: (reusing the already-booted distributed-lock MongoDB connection) or in
        #: tests. Resolved lazily at start() when not provided.
        self._state_db: Any = state_db

    async def start(self) -> None:
        """Start the monitor and subscribe to heartbeats."""
        # tradeengine#533: restore persisted restricted_mode BEFORE connecting so
        # the fail-safe is correct from the first instant of boot, and the
        # restricted_mode_status gauge reflects the restored state immediately.
        await self._restore_state()
        try:
            # AC: Use robust NATS connection parameters for parity with consumer
            self.nats_client = await nats.connect(
                self.nats_url,
                connect_timeout=10,
                max_reconnect_attempts=10,
                reconnect_time_wait=2,
                ping_interval=20,
                allow_reconnect=True,
                name="tradeengine-heartbeat-monitor",
            )
            await self.nats_client.subscribe(self.subject, cb=self._message_handler)

            # AC: Set initial heartbeat time to start time to detect initial timeout
            self.last_heartbeat_time = time.time()
            self.is_running = True
            self._monitor_task = asyncio.create_task(self._check_timeout_loop())
            logger.info(f"HeartbeatMonitor started, monitoring {self.subject}")
        except Exception as e:
            logger.error(f"HeartbeatMonitor failed to start: {e}")
            self.is_running = False
            # AC: Enter restricted mode if monitor fails to start
            await self._enter_restricted_mode()

    async def stop(self) -> None:
        """Stop the monitor and cleanup."""
        self.is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        if self.nats_client:
            await self.nats_client.close()
            self.nats_client = None

    async def _message_handler(self, msg: Any) -> None:
        """Handle incoming heartbeat messages."""
        try:
            # AC: Use model validation for heartbeats
            data = json.loads(msg.data.decode())
            HeartbeatMessage.model_validate(data)

            self.last_heartbeat_time = time.time()

            # Export heartbeat metric
            last_heartbeat_received_timestamp.labels(
                service=data.get("service", "unknown"), subject=self.subject
            ).set(self.last_heartbeat_time)

            if self.restricted_mode:
                self.consecutive_heartbeats += 1
                if self.consecutive_heartbeats >= self.recovery_threshold:
                    await self._exit_restricted_mode()
            else:
                self.consecutive_heartbeats = 0

        except Exception as e:
            logger.error(f"HeartbeatMonitor failed to parse/validate message: {e}")
            # Reset consecutive heartbeats on invalid message
            self.consecutive_heartbeats = 0

    async def _check_timeout_loop(self) -> None:
        """Background task to check for heartbeat timeouts."""
        while self.is_running:
            try:
                await asyncio.sleep(5.0)
                # Ensure the metric is initialized/updated
                restricted_mode_status.set(1 if self.restricted_mode else 0)

                if not self.restricted_mode and self.last_heartbeat_time > 0:
                    if (time.time() - self.last_heartbeat_time) > self.timeout:
                        await self._enter_restricted_mode()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat timeout loop: {e}")

    async def _enter_restricted_mode(self) -> None:
        """Enter RESTRICTED_MODE fail-safe."""
        if not self.restricted_mode:
            self.restricted_mode = True
            self.consecutive_heartbeats = 0
            restricted_mode_status.set(1)
            logger.critical("🚨 ENTERING RESTRICTED_MODE: CIO heartbeat lost!")
            # tradeengine#533: durably record the transition so a restart while
            # restricted boots restricted (fail-safe), not permissive.
            await self._persist_state(True)

    async def _exit_restricted_mode(self) -> None:
        """Exit RESTRICTED_MODE and return to NORMAL_MODE."""
        if self.restricted_mode:
            self.restricted_mode = False
            self.consecutive_heartbeats = 0
            restricted_mode_status.set(0)
            logger.info("✅ EXITING RESTRICTED_MODE: CIO heartbeat recovered.")
            # tradeengine#533: persist the recovery so a subsequent restart does
            # not resurrect a stale restricted state.
            await self._persist_state(False)

    async def _resolve_state_db(self) -> Any:
        """Return the MongoDB handle for persistence, or ``None`` if unavailable.

        Prefers an injected handle; otherwise reuses the already-booted
        distributed-lock MongoDB connection (dispatcher initializes it before
        starting the heartbeat monitor). Never raises.
        """
        if self._state_db is not None:
            return self._state_db
        try:
            from shared.distributed_lock import distributed_lock_manager

            await distributed_lock_manager._ensure_mongodb_connected()
            self._state_db = distributed_lock_manager.mongodb_db
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"HeartbeatMonitor could not resolve state DB: {e}")
            self._state_db = None
        return self._state_db

    async def _persist_state(self, restricted: bool) -> None:
        """Persist the current restricted-mode flag to durable storage.

        No-op when persistence is disabled. Best-effort: a persistence failure
        is logged but never propagates — losing a write must not crash the
        monitor. The next transition (or the timeout loop) will retry.
        """
        if not self.persist_enabled:
            return
        try:
            db = await self._resolve_state_db()
            if db is None:
                logger.warning(
                    "HeartbeatMonitor: no MongoDB handle; restricted_mode "
                    f"transition to {restricted} not persisted."
                )
                return
            await db[HEARTBEAT_STATE_COLLECTION].update_one(
                {"_id": HEARTBEAT_STATE_DOC_ID},
                {"$set": {"restricted_mode": restricted, "updated_at": time.time()}},
                upsert=True,
            )
        except Exception as e:
            logger.error(f"HeartbeatMonitor failed to persist restricted_mode: {e}")

    async def _restore_state(self) -> None:
        """Restore restricted_mode from durable storage at boot.

        Fail-safe semantics (tradeengine#533):
          * persistence disabled -> legacy in-memory init (stay NORMAL).
          * persisted doc found  -> restore its ``restricted_mode`` verbatim.
          * no persisted doc     -> NORMAL (first-ever boot; nothing to restore).
          * state UNVERIFIABLE   -> RESTRICTED (fail-closed): the store is
            unreachable/erroring, so we cannot prove it is safe to trade.

        Always publishes the resulting state to the ``restricted_mode_status``
        gauge so monitoring reflects the restored value immediately on boot.
        """
        if not self.persist_enabled:
            return
        try:
            db = await self._resolve_state_db()
            if db is None:
                # Cannot verify last-known state -> fail closed.
                self.restricted_mode = True
                logger.critical(
                    "🚨 HeartbeatMonitor: restricted_mode state UNVERIFIABLE at "
                    "boot (no MongoDB handle) — defaulting to RESTRICTED (fail-safe)."
                )
            else:
                doc = await db[HEARTBEAT_STATE_COLLECTION].find_one(
                    {"_id": HEARTBEAT_STATE_DOC_ID}
                )
                if doc is None:
                    # No prior state persisted — genuine first boot, stay NORMAL.
                    self.restricted_mode = False
                    logger.info(
                        "HeartbeatMonitor: no persisted restricted_mode state; "
                        "starting in NORMAL mode."
                    )
                else:
                    self.restricted_mode = bool(doc.get("restricted_mode", True))
                    logger.info(
                        "HeartbeatMonitor: restored restricted_mode="
                        f"{self.restricted_mode} from durable store."
                    )
        except Exception as e:
            # Any read error means we cannot verify the state -> fail closed.
            self.restricted_mode = True
            logger.critical(
                "🚨 HeartbeatMonitor: restricted_mode state UNVERIFIABLE at boot "
                f"({e}) — defaulting to RESTRICTED (fail-safe)."
            )
        finally:
            self.consecutive_heartbeats = 0
            # AC: gauge reflects restored state immediately on boot.
            restricted_mode_status.set(1 if self.restricted_mode else 0)

    def is_restricted(self) -> bool:
        """Check if TradeEngine is in RESTRICTED_MODE."""
        return self.restricted_mode
