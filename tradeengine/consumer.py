import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import nats
import nats.aio.client
import nats.aio.subscription
from opentelemetry import context, trace
from petrosa_otel import extract_trace_context
from prometheus_client import Counter

from contracts.signal import Signal
from shared.config import settings
from tradeengine.dispatcher import Dispatcher

logger = logging.getLogger(__name__)

# OpenTelemetry tracer
tracer = trace.get_tracer(__name__)

# Prometheus metrics
messages_processed = Counter(
    "tradeengine_nats_messages_processed_total",
    "Total NATS messages processed",
    ["status"],
)
nats_errors = Counter("tradeengine_nats_errors_total", "Total NATS errors", ["type"])


class SignalConsumer:
    """NATS consumer for trading signals"""

    def __init__(self, dispatcher: Dispatcher | None = None) -> None:
        self.nc: nats.aio.client.Client | None = None
        self.running: bool = False
        self.subscription: nats.aio.subscription.Subscription | None = None
        self.dispatcher = dispatcher  # Use provided dispatcher or None
        self._dispatcher_provided = dispatcher is not None

    async def initialize(self, dispatcher: Dispatcher | None = None) -> bool:
        """Initialize NATS connection"""
        try:
            # Use provided dispatcher if given
            if dispatcher:
                self.dispatcher = dispatcher
                self._dispatcher_provided = True
                logger.info("✅ Using provided dispatcher with configured exchange")
            elif not self.dispatcher:
                # Create dispatcher without exchange as fallback
                self.dispatcher = Dispatcher()
                self._dispatcher_provided = False
                logger.warning(
                    "⚠️  Creating dispatcher without exchange - "
                    "orders will be tracked locally only"
                )

            # Check if NATS is enabled
            if not settings.nats_enabled:
                logger.info("NATS is disabled - skipping NATS consumer initialization")
                return False

            if not settings.nats_servers:
                logger.error("NATS is enabled but no servers configured")
                return False

            # Connect to NATS with proper reconnection and keep-alive settings
            from shared.constants import (
                NATS_CONNECT_TIMEOUT,
                NATS_MAX_RECONNECT_ATTEMPTS,
                NATS_RECONNECT_TIME_WAIT,
            )

            self.nc = await nats.connect(
                servers=settings.nats_servers,
                connect_timeout=NATS_CONNECT_TIMEOUT,
                max_reconnect_attempts=NATS_MAX_RECONNECT_ATTEMPTS,
                reconnect_time_wait=NATS_RECONNECT_TIME_WAIT,
                ping_interval=60,  # Send ping every 60 seconds
                max_outstanding_pings=3,  # Allow 3 missed pings before closing
                allow_reconnect=True,
                name="petrosa-tradeengine-consumer",  # Name for monitoring
            )

            logger.info(
                "NATS consumer connected with reconnect enabled | "
                "Server: %s | Max reconnect: %d | Ping interval: 60s",
                settings.nats_servers,
                NATS_MAX_RECONNECT_ATTEMPTS,
            )

            # Only initialize dispatcher if we created it ourselves
            if not self._dispatcher_provided and self.dispatcher:
                await self.dispatcher.initialize()

            return True
        except Exception as e:
            logger.error("Failed to initialize NATS consumer: %s", str(e))
            nats_errors.labels(type="initialization").inc()
            return False

    async def start_consuming(self) -> None:
        """Start consuming messages from NATS with enhanced logging"""
        # Check if NATS is enabled
        if not settings.nats_enabled:
            logger.info("⚙️  NATS is disabled - consumer will not start")
            return

        if not self.nc:
            if not await self.initialize():
                logger.error(
                    "❌ Cannot start consuming - NATS consumer not initialized"
                )
                return
        if self.nc is None:
            raise RuntimeError(
                "NATS client is not initialized after initialization attempt"
            )

        self.running = True
        logger.info(
            "🚀 STARTING NATS CONSUMER | Subject: %s | No queue group (duplicate detection in dispatcher)",
            settings.nats_signal_subject,
        )

        try:
            # Subscribe to the signal subject
            logger.info(
                "Subscribing to subject: %s with callback: %s",
                settings.nats_signal_subject,
                self._message_handler,
            )
            # Remove queue group - it's preventing message delivery
            # Each pod will receive all messages, but dispatcher has duplicate detection
            self.subscription = await self.nc.subscribe(
                settings.nats_signal_subject,
                cb=self._message_handler,
            )

            logger.info(
                "✅ NATS SUBSCRIPTION ACTIVE | Subject: %s | Waiting for signals...",
                settings.nats_signal_subject,
            )
            logger.info("Subscription details: %s", self.subscription)

            # Keep the consumer running
            logger.info("Entering consumer loop...")
            loop_counter = 0
            while self.running:
                await asyncio.sleep(1)
                loop_counter += 1
                # Periodic heartbeat log every 30 seconds (more frequent for debugging)
                if loop_counter % 30 == 0:
                    logger.info(
                        "💓 NATS consumer heartbeat | Loop #%d | Running: %s | NC connected: %s | Subscription: %s",
                        loop_counter,
                        self.running,
                        self.nc.is_connected if self.nc else False,
                        self.subscription is not None,
                    )

            logger.info("NATS consumer loop exited (self.running=False)")

        except Exception as e:
            logger.error("❌ NATS CONSUMER ERROR | Error: %s", str(e), exc_info=True)
            nats_errors.labels(type="consumer_loop").inc()
        finally:
            logger.info("NATS consumer cleanup starting...")
            await self.stop_consuming()
            logger.info("NATS consumer cleanup completed")

    async def _message_handler(self, msg: Any) -> None:
        """Handle incoming NATS messages with trace context extraction"""
        # CRITICAL: Log at the very start to see if handler is called at all
        print(
            f"🔥 HANDLER CALLED! Subject: {msg.subject if msg else 'None'}", flush=True
        )
        try:
            logger.info(
                "📨 NATS MESSAGE RECEIVED | Subject: %s | Size: %d bytes",
                msg.subject,
                len(msg.data),
            )

            # Parse message into Signal
            signal_data = json.loads(msg.data.decode())
            logger.info(
                "📊 PARSING SIGNAL | Strategy: %s | Symbol: %s | Action: %s",
                signal_data.get("strategy_id", "Unknown"),
                signal_data.get("symbol", "Unknown"),
                signal_data.get("action", "Unknown"),
            )

            # Extract trace context from signal message (with error handling)
            # Support both _otel_trace_context (petrosa_otel standard) and _otel_trace_headers (ta-bot legacy)
            try:
                ctx = extract_trace_context(signal_data)

                # Fallback: Check for legacy _otel_trace_headers field if _otel_trace_context not found
                if (
                    ctx == context.get_current()
                    and "_otel_trace_headers" in signal_data
                ):
                    from opentelemetry.propagate import extract

                    legacy_carrier = signal_data.get("_otel_trace_headers", {})
                    if legacy_carrier:
                        logger.debug(
                            "Found legacy trace context field _otel_trace_headers, extracting..."
                        )
                        ctx = extract(legacy_carrier)
            except Exception as e:
                logger.warning(
                    "Failed to extract trace context, using current context: %s", e
                )
                ctx = None

            # Create span with extracted context for distributed tracing
            with tracer.start_as_current_span(
                "process_trading_signal",
                context=ctx,
                kind=trace.SpanKind.CONSUMER,
            ) as span:
                try:
                    # Set messaging attributes for observability
                    span.set_attribute("messaging.system", "nats")
                    span.set_attribute("messaging.destination", "signals.trading")
                    span.set_attribute("messaging.operation", "receive")
                    span.set_attribute(
                        "signal.strategy_id", signal_data.get("strategy_id", "unknown")
                    )
                    span.set_attribute(
                        "signal.symbol", signal_data.get("symbol", "unknown")
                    )
                    span.set_attribute(
                        "signal.action", signal_data.get("action", "unknown")
                    )

                    # Parse timestamp with better error handling and fallback
                    timestamp_raw = signal_data.get("timestamp")
                    if not timestamp_raw:
                        logger.warning(
                            "⚠️ MISSING TIMESTAMP | Subject: %s | Using current time as fallback",
                            msg.subject,
                        )
                        signal_data["timestamp"] = datetime.utcnow()
                    else:
                        try:
                            signal_data["timestamp"] = datetime.fromisoformat(
                                timestamp_raw
                            )
                        except (ValueError, TypeError) as e:
                            logger.warning(
                                "⚠️ INVALID TIMESTAMP FORMAT | Subject: %s | Timestamp: %s | Type: %s | Error: %s | Using current time as fallback",
                                msg.subject,
                                timestamp_raw,
                                type(timestamp_raw).__name__,
                                str(e),
                            )
                            # Use current time as fallback instead of raising error
                            signal_data["timestamp"] = datetime.utcnow()

                    signal = Signal(**signal_data)

                    logger.info(
                        "✅ SIGNAL PARSED SUCCESSFULLY | %s | %s %s @ %s",
                        signal.strategy_id,
                        signal.symbol,
                        signal.action.upper(),
                        signal.current_price,
                    )

                    # Dispatch signal within trace context
                    logger.info("🔄 DISPATCHING SIGNAL: %s", signal.strategy_id)
                    print(
                        f"🔥 About to dispatch signal: {signal.strategy_id}", flush=True
                    )
                    if not self.dispatcher:
                        raise RuntimeError("Dispatcher not initialized")
                    result = await self.dispatcher.dispatch(signal)
                    print(
                        f"🔥 Dispatch completed for: {signal.strategy_id}", flush=True
                    )

                    messages_processed.labels(status="success").inc()
                    logger.info(
                        "✅ NATS MESSAGE PROCESSED | Signal: %s | Status: %s | Result: %s",
                        signal.strategy_id,
                        result.get("status"),
                        result,
                    )
                    print(
                        f"🔥 Handler completing successfully for: {signal.strategy_id}",
                        flush=True,
                    )

                    # Send acknowledgment with timeout to prevent blocking handler
                    if msg.reply and self.nc:
                        try:
                            response = {
                                "status": "processed",
                                "signal_id": signal.strategy_id,
                                "result": result,
                            }
                            await asyncio.wait_for(
                                self.nc.publish(
                                    msg.reply, json.dumps(response).encode()
                                ),
                                timeout=0.5,  # 500ms timeout
                            )
                            logger.info("📤 NATS ACK SENT to %s", msg.reply)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "⏱️ ACK publish timed out for %s - continuing anyway",
                                msg.reply,
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to send ACK to %s: %s - continuing anyway",
                                msg.reply,
                                e,
                            )

                    # Mark span as successful
                    span.set_status(trace.Status(trace.StatusCode.OK))

                except Exception as processing_error:
                    # Mark span as error
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"Signal processing failed: {processing_error}",
                        )
                    )
                    span.record_exception(processing_error)
                    # Re-raise to be caught by outer handler
                    raise

        except json.JSONDecodeError as e:
            logger.error(
                "❌ JSON DECODE ERROR | Subject: %s | Error: %s | Raw data: %s",
                msg.subject,
                str(e),
                msg.data.decode()[:200],
            )
            messages_processed.labels(status="error").inc()
            nats_errors.labels(type="processing").inc()

        except Exception as e:
            print(f"🔥 HANDLER EXCEPTION: {e}", flush=True)
            logger.error(
                "❌ NATS MESSAGE PROCESSING FAILED | Subject: %s | Error: %s",
                msg.subject,
                str(e),
                exc_info=True,
            )
            messages_processed.labels(status="error").inc()
            nats_errors.labels(type="processing").inc()

            # Send error response with timeout to prevent blocking
            if msg.reply and self.nc:
                try:
                    error_response = {"status": "error", "error": str(e)}
                    await asyncio.wait_for(
                        self.nc.publish(msg.reply, json.dumps(error_response).encode()),
                        timeout=0.5,
                    )
                    logger.info("📤 NATS ERROR RESPONSE SENT to %s", msg.reply)
                except (asyncio.TimeoutError, Exception):
                    pass  # Don't block on error ACK

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
    if not settings.nats_enabled:
        logger.info("NATS is disabled - consumer will not run")
        return

    logger.info("Starting Petrosa NATS Signal Consumer...")
    await signal_consumer.start_consuming()


if __name__ == "__main__":
    # Run the consumer
    asyncio.run(run_consumer())
