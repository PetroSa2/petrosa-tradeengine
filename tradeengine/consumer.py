import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import nats
import nats.aio.client
import nats.aio.subscription
from prometheus_client import Counter

from contracts.signal import Signal
from shared.config import settings
from tradeengine.dispatcher import Dispatcher

logger = logging.getLogger(__name__)

# Prometheus metrics
messages_processed = Counter(
    "tradeengine_nats_messages_processed_total",
    "Total NATS messages processed",
    ["status"],
)
nats_errors = Counter("tradeengine_nats_errors_total", "Total NATS errors", ["type"])


class SignalConsumer:
    """NATS consumer for trading signals"""

    def __init__(self) -> None:
        self.nc: nats.aio.client.Client | None = None
        self.running: bool = False
        self.subscription: nats.aio.subscription.Subscription | None = None
        self.dispatcher = Dispatcher()

    async def initialize(self) -> bool:
        """Initialize NATS connection"""
        try:
            self.nc = await nats.connect(settings.nats_servers)
            await self.dispatcher.initialize()
            logger.info(
                "NATS consumer initialized, connected to: %s", settings.nats_servers
            )
            return True
        except Exception as e:
            logger.error("Failed to initialize NATS consumer: %s", str(e))
            nats_errors.labels(type="initialization").inc()
            return False

    async def start_consuming(self) -> None:
        """Start consuming messages from NATS"""
        if not self.nc:
            if not await self.initialize():
                logger.error("Cannot start consuming - NATS consumer not initialized")
                return
        if self.nc is None:
            raise RuntimeError(
                "NATS client is not initialized after initialization attempt"
            )

        self.running = True
        logger.info(
            "Starting NATS consumer for subject: %s", settings.nats_signal_subject
        )

        try:
            # Subscribe to the signal subject
            self.subscription = await self.nc.subscribe(
                settings.nats_signal_subject,
                cb=self._message_handler,
                queue="petrosa-tradeengine",  # Load balancing across instances
            )

            logger.info(
                "NATS subscription created for subject: %s",
                settings.nats_signal_subject,
            )

            # Keep the consumer running
            while self.running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("Error in NATS consumer loop: %s", str(e))
            nats_errors.labels(type="consumer_loop").inc()
        finally:
            await self.stop_consuming()

    async def _message_handler(self, msg: Any) -> None:
        """Handle incoming NATS messages"""
        try:
            logger.info("Processing NATS message from subject: %s", msg.subject)

            # Parse message into Signal
            signal_data = json.loads(msg.data.decode())
            signal_data["timestamp"] = datetime.fromisoformat(signal_data["timestamp"])
            signal = Signal(**signal_data)

            # Dispatch signal
            result = await self.dispatcher.dispatch(signal)

            messages_processed.labels(status="success").inc()
            logger.info("Successfully processed signal: %s", signal.strategy_id)

            # Send acknowledgment if reply subject is provided
            if msg.reply and self.nc:
                response = {
                    "status": "processed",
                    "signal_id": signal.strategy_id,
                    "result": result,
                }
                await self.nc.publish(msg.reply, json.dumps(response).encode())

        except Exception as e:
            logger.error("Failed to process NATS message: %s", str(e))
            messages_processed.labels(status="error").inc()
            nats_errors.labels(type="processing").inc()

            # Send error response if reply subject is provided
            if msg.reply and self.nc:
                error_response = {"status": "error", "error": str(e)}
                await self.nc.publish(msg.reply, json.dumps(error_response).encode())

    async def stop_consuming(self) -> None:
        """Stop the consumer"""
        self.running = False

        if self.subscription:
            await self.subscription.unsubscribe()
            logger.info("NATS subscription closed")

        if self.nc:
            await self.nc.close()
            logger.info("NATS consumer stopped")


# Global consumer instance
signal_consumer = SignalConsumer()


async def run_consumer() -> None:
    """Run the NATS consumer in a separate process/service"""
    logger.info("Starting Petrosa NATS Signal Consumer...")
    await signal_consumer.start_consuming()


if __name__ == "__main__":
    # Run the consumer
    asyncio.run(run_consumer())
