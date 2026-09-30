"""
Binance Rate Limit Monitor Service.
Captures used weight from API headers and broadcasts via NATS.
"""

import asyncio
import json
import logging
import re
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
        self.limit_1m: int = 2400
        self.pause_until: float = 0.0
        self.backoff_ratio: float = 0.8

    @property
    def polling_paused(self) -> bool:
        """Whether non-essential exchange polling is currently suspended."""
        return time.time() < self.pause_until

    async def wait_for_polling(self) -> None:
        """Delay a non-essential poll while weight is near exhaustion or banned."""
        if self.last_weight >= self.limit_1m * self.backoff_ratio:
            self.pause_until = max(self.pause_until, time.time() + 1.0)
        delay = self.pause_until - time.time()
        if delay > 0:
            logger.warning("Binance polling paused for %.1fs", delay)
            await asyncio.sleep(delay)

    def record_error(self, error: Exception) -> None:
        """Pause polling after a Binance rate-limit or IP-ban response."""
        code = getattr(error, "code", None)
        status = getattr(error, "status_code", None)
        text = " ".join(
            str(value) for value in (getattr(error, "message", ""), error) if value
        )
        if code != -1003 and status != 418:
            return
        match = re.search(r"banned\s+until\s+(\d{10,})", text, re.IGNORECASE)
        if match:
            raw_until = int(match.group(1))
            until = raw_until / 1000 if raw_until > 10_000_000_000 else raw_until
            self.pause_until = max(self.pause_until, until)
        else:
            self.pause_until = max(self.pause_until, time.time() + 60)
        logger.error(
            "Binance polling paused until %.3f after error %s",
            self.pause_until,
            code or status,
        )

    async def start(self) -> None:
        """Start the monitor and connect to NATS."""
        try:
            self.nats_client = await nats.connect(self.nats_url)
            logger.info(f"RateLimitMonitor connected to NATS at {self.nats_url}")
        except Exception as e:
            logger.error(f"RateLimitMonitor failed to connect to NATS: {e}")
            self.nats_client = None

    async def stop(self) -> None:
        """Stop the monitor and close NATS connection."""
        if self.nats_client:
            await self.nats_client.close()
            self.nats_client = None

    async def update_from_headers(self, headers: dict[str, str]) -> None:
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
                try:
                    from tradeengine.metrics import binance_used_weight_1m

                    binance_used_weight_1m.labels(service="tradeengine").set(weight)
                except (
                    Exception
                ):  # pragma: no cover - metrics must not break the hot path
                    pass
                await self._broadcast(weight)
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse rate limit weight: {e}")

    async def _broadcast(self, weight: int) -> None:
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
