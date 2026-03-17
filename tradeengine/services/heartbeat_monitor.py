"""
Heartbeat Monitor Service for TradeEngine.
Monitors CIO heartbeat and manages RESTRICTED_MODE fail-safe.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import nats
import nats.aio.client
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HeartbeatMessage(BaseModel):
    """Standardized heartbeat message model."""
    service: str
    timestamp: float = Field(default_factory=time.time)
    version: str = "1.0.0"
    status: str = "healthy"


class HeartbeatMonitor:
    """Monitors ecosystem heartbeats and manages fail-safe modes."""

    def __init__(
        self, 
        nats_url: str, 
        subject: str = "cio.nurse.heartbeat",
        timeout: float = 60.0,
        recovery_threshold: int = 3
    ):
        self.nats_url = nats_url
        self.subject = subject
        self.timeout = timeout
        self.recovery_threshold = recovery_threshold
        
        self.nats_client: Optional[nats.aio.client.Client] = None
        self.last_heartbeat_time: float = 0
        self.consecutive_heartbeats: int = 0
        self.restricted_mode: bool = False
        self.is_running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the monitor and subscribe to heartbeats."""
        try:
            self.nats_client = await nats.connect(self.nats_url)
            await self.nats_client.subscribe(self.subject, cb=self._message_handler)
            self.is_running = True
            self._monitor_task = asyncio.create_task(self._check_timeout_loop())
            logger.info(f"HeartbeatMonitor started, monitoring {self.subject}")
        except Exception as e:
            logger.error(f"HeartbeatMonitor failed to start: {e}")
            self.is_running = False

    async def stop(self):
        """Stop the monitor and cleanup."""
        self.is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        if self.nats_client:
            await self.nats_client.close()
            self.nats_client = None

    async def _message_handler(self, msg):
        """Handle incoming heartbeat messages."""
        try:
            # We don't necessarily need to parse the content to know it is alive,
            # but we do it for validation.
            data = json.loads(msg.data.decode())
            self.last_heartbeat_time = time.time()
            
            if self.restricted_mode:
                self.consecutive_heartbeats += 1
                if self.consecutive_heartbeats >= self.recovery_threshold:
                    await self._exit_restricted_mode()
            else:
                self.consecutive_heartbeats = 0
                
        except Exception as e:
            logger.error(f"HeartbeatMonitor failed to parse message: {e}")

    async def _check_timeout_loop(self):
        """Background task to check for heartbeat timeouts."""
        while self.is_running:
            try:
                await asyncio.sleep(5.0)
                if not self.restricted_mode and self.last_heartbeat_time > 0:
                    if (time.time() - self.last_heartbeat_time) > self.timeout:
                        await self._enter_restricted_mode()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat timeout loop: {e}")

    async def _enter_restricted_mode(self):
        """Enter RESTRICTED_MODE fail-safe."""
        self.restricted_mode = True
        self.consecutive_heartbeats = 0
        logger.critical("🚨 ENTERING RESTRICTED_MODE: CIO heartbeat lost!")
        # TODO: Trigger P1 alert via RedundantAlertDispatcher
        # For now, we log it heavily.

    async def _exit_restricted_mode(self):
        """Exit RESTRICTED_MODE and return to NORMAL_MODE."""
        self.restricted_mode = False
        self.consecutive_heartbeats = 0
        logger.info("✅ EXITING RESTRICTED_MODE: CIO heartbeat recovered.")

    def is_restricted(self) -> bool:
        """Check if TradeEngine is in RESTRICTED_MODE."""
        # If we haven't started or had a first heartbeat yet, we might want to start restricted
        # but the AC says "If no heartbeat is received for > 60s", implying we need to miss it first.
        return self.restricted_mode
