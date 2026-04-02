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


class HeartbeatMonitor:
    """Monitors ecosystem heartbeats and manages fail-safe modes."""

    def __init__(
        self,
        nats_url: str,
        subject: str = "cio.heartbeat",
        timeout: float | None = None,
        recovery_threshold: int | None = None,
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
        self._monitor_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the monitor and subscribe to heartbeats."""
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

    async def _exit_restricted_mode(self) -> None:
        """Exit RESTRICTED_MODE and return to NORMAL_MODE."""
        if self.restricted_mode:
            self.restricted_mode = False
            self.consecutive_heartbeats = 0
            restricted_mode_status.set(0)
            logger.info("✅ EXITING RESTRICTED_MODE: CIO heartbeat recovered.")

    def is_restricted(self) -> bool:
        """Check if TradeEngine is in RESTRICTED_MODE."""
        return self.restricted_mode
