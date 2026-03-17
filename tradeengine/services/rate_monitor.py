"""
Binance Rate Limit Monitor Service.
Captures used weight from API headers and broadcasts via NATS.
"""

import json
import logging
import time
from typing import Any, Optional

import nats
import nats.aio.client
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RateLimitStatus(BaseModel):
    """Rate limit status model."""

    weight_1m: int = Field(..., description="Used weight in the last 1 minute")
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp")


class RateLimitMonitor:
    """Monitors and broadcasts Binance API rate limits."""

    def __init__(self, nats_url: str, subject: str = "exchange.binance.rate_limits"):
        self.nats_url = nats_url
        self.subject = subject
        self.nats_client: nats.aio.client.Client | None = None
        self.last_weight: int = 0
        self.last_update_time: float = 0
        self.update_interval: float = 5.0  # seconds

    async def start(self):
        """Start the monitor and connect to NATS."""
        try:
            self.nats_client = await nats.connect(self.nats_url)
            logger.info(f"RateLimitMonitor connected to NATS at {self.nats_url}")
        except Exception as e:
            logger.error(f"RateLimitMonitor failed to connect to NATS: {e}")
            self.nats_client = None

    async def stop(self):
        """Stop the monitor and close NATS connection."""
        if self.nats_client:
            await self.nats_client.close()
            self.nats_client = None

    async def update_from_headers(self, headers: dict[str, str]):
        """Update used weight from response headers and broadcast if changed."""
        weight_str = headers.get("x-mbx-used-weight-1m") or headers.get(
            "X-MBX-USED-WEIGHT-1M"
        )

        if not weight_str:
            return

        try:
            weight = int(weight_str)
            now = time.time()

            # Broadcast if weight changed or interval elapsed
            if (
                weight != self.last_weight
                or (now - self.last_update_time) >= self.update_interval
            ):
                self.last_weight = weight
                self.last_update_time = now
                await self._broadcast(weight)
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse rate limit weight: {e}")

    async def _broadcast(self, weight: int):
        """Broadcast rate limit status to NATS."""
        if not self.nats_client:
            # Try to reconnect if client is missing
            await self.start()
            if not self.nats_client:
                return

        status = RateLimitStatus(weight_1m=weight)
        message = status.model_dump_json()

        try:
            await self.nats_client.publish(self.subject, message.encode())
            logger.debug(f"Broadcasted rate limit: {weight} to {self.subject}")
        except Exception as e:
            logger.error(f"Failed to broadcast rate limit: {e}")
