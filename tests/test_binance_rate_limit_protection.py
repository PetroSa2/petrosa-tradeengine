import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tradeengine.services.rate_monitor import RateLimitMonitor


@pytest.mark.asyncio
async def test_used_weight_slows_nonessential_polling(monkeypatch):
    monitor = RateLimitMonitor("nats://unused")
    monitor._broadcast = AsyncMock()
    await monitor.update_from_headers({"X-MBX-USED-WEIGHT-1M": "1920"})

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("tradeengine.services.rate_monitor.asyncio.sleep", fake_sleep)
    await monitor.wait_for_polling()

    assert sleeps and sleeps[0] > 0


def test_binance_ban_pauses_until_parsed_expiry():
    monitor = RateLimitMonitor("nats://unused")
    banned_until_ms = int((time.time() + 123) * 1000)
    error = SimpleNamespace(
        code=-1003,
        status_code=418,
        message=f"IP banned until {banned_until_ms}",
    )

    monitor.record_error(error)

    assert monitor.pause_until >= banned_until_ms / 1000
    assert monitor.polling_paused


def test_non_rate_limit_errors_do_not_pause_polling():
    monitor = RateLimitMonitor("nats://unused")
    monitor.record_error(
        SimpleNamespace(code=-2010, status_code=400, message="rejected")
    )

    assert not monitor.polling_paused
