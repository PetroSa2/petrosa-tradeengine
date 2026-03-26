import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.constants import UTC
from tradeengine.services.heartbeat_monitor import HeartbeatMessage, HeartbeatMonitor


@pytest.mark.asyncio
async def test_heartbeat_monitor_lifecycle():
    """Test that HeartbeatMonitor starts and stops cleanly."""
    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        mock_nc = mock_connect.return_value
        monitor = HeartbeatMonitor(
            nats_url="nats://localhost:4222", subject="test.heartbeat"
        )

        await monitor.start()
        assert monitor.is_running is True
        mock_connect.assert_called_once()
        mock_nc.subscribe.assert_called_once_with(
            "test.heartbeat", cb=monitor._message_handler
        )

        await monitor.stop()
        assert monitor.is_running is False
        mock_nc.close.assert_called_once()


@pytest.mark.asyncio
async def test_heartbeat_monitor_restricted_mode_entry_on_timeout():
    """Test that HeartbeatMonitor enters restricted mode on timeout."""
    with patch("nats.connect", new_callable=AsyncMock):
        monitor = HeartbeatMonitor(
            nats_url="nats://localhost:4222", subject="test.heartbeat", timeout=0.1
        )

        await monitor.start()
        assert monitor.is_restricted() is False

        # Initial heartbeat time is set on start, so we wait for timeout
        await asyncio.sleep(0.2)
        # We need to manually trigger the check since the loop runs every 5s
        await monitor._check_timeout_loop_once()

        assert monitor.is_restricted() is True
        await monitor.stop()


@pytest.mark.asyncio
async def test_heartbeat_monitor_recovery():
    """Test that HeartbeatMonitor exits restricted mode after N heartbeats."""
    with patch("nats.connect", new_callable=AsyncMock):
        monitor = HeartbeatMonitor(
            nats_url="nats://localhost:4222",
            subject="test.heartbeat",
            recovery_threshold=3,
        )

        # Force restricted mode
        await monitor._enter_restricted_mode()
        assert monitor.is_restricted() is True

        # Send 2 heartbeats (not enough for recovery)
        mock_msg = MagicMock()
        heartbeat_data = HeartbeatMessage(service="cio").to_json().encode()
        mock_msg.data = heartbeat_data

        await monitor._message_handler(mock_msg)
        await monitor._message_handler(mock_msg)
        assert monitor.is_restricted() is True
        assert monitor.consecutive_heartbeats == 2

        # Send 3rd heartbeat (recovery)
        await monitor._message_handler(mock_msg)
        assert monitor.is_restricted() is False
        assert monitor.consecutive_heartbeats == 0


# Helper to run one iteration of the timeout loop for testing
async def _check_timeout_loop_once(self):
    if not self.restricted_mode and self.last_heartbeat_time > 0:
        if (time.time() - self.last_heartbeat_time) > self.timeout:
            await self._enter_restricted_mode()


# Patch the class for testing
HeartbeatMonitor._check_timeout_loop_once = _check_timeout_loop_once
