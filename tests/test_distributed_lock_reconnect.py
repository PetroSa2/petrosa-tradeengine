"""Lease API behavior for the distributed lock manager."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from shared.distributed_lock import (
    DistributedLockManager,
    LeaseClient,
    LockState,
    LockUnavailableError,
    lease_retry_budget_seconds,
    validate_lease_ttl_budget,
)


@pytest.fixture
def transport():
    return httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"acquired": True, "released": True}, request=request
        )
    )


@pytest.mark.asyncio
async def test_acquire_posts_owner_and_ttl(transport):
    client = LeaseClient(timeout=1.5)
    client._client._client = httpx.AsyncClient(
        transport=transport, base_url=client.base_url
    )
    response = await client.acquire("signal-x", "pod-a", 60)
    assert response["acquired"] is True
    await client.close()


@pytest.mark.asyncio
async def test_renew_retries_one_timeout_and_succeeds():
    attempts = 0

    def request_handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("upstream stalled", request=request)
        return httpx.Response(200, json={"renewed": True}, request=request)

    client = LeaseClient(timeout=0.01)
    client._client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(request_handler), base_url=client.base_url
    )
    response = await client.renew("tradeengine-leader", "pod-a", 30)

    assert response["renewed"] is True
    assert attempts == 2
    await client.close()


def test_retry_budget_stays_below_leader_ttl():
    budget = lease_retry_budget_seconds(5.0)

    assert budget < 30
    validate_lease_ttl_budget(30, 5.0)


@pytest.mark.asyncio
async def test_timeout_log_contains_exception_type(caplog):
    manager = DistributedLockManager()
    manager.lease_client.renew = AsyncMock(
        side_effect=httpx.ReadTimeout("upstream stalled")
    )
    manager.is_leader = True
    manager.heartbeat_interval = 0

    task = asyncio.create_task(manager._heartbeat_loop())
    await asyncio.sleep(0)
    await task

    assert "ReadTimeout" in caplog.text
    await manager.close()


@pytest.mark.asyncio
async def test_held_lease_is_not_unavailable(monkeypatch):
    manager = DistributedLockManager()
    manager.lease_client.acquire = AsyncMock(
        return_value={"acquired": False, "owner": "pod-b"}
    )
    assert await manager.acquire_lock("signal-x") is LockState.HELD
    await manager.close()


@pytest.mark.asyncio
async def test_unavailable_lease_fails_closed(monkeypatch):
    manager = DistributedLockManager()
    manager.lease_client.acquire = AsyncMock(side_effect=TimeoutError("slow"))
    assert await manager.acquire_lock("signal-x") is LockState.UNAVAILABLE
    with pytest.raises(LockUnavailableError):
        await manager.execute_with_lock("signal-x", AsyncMock())
    await manager.close()


@pytest.mark.asyncio
async def test_release_is_best_effort():
    manager = DistributedLockManager()
    manager.lease_client.release = AsyncMock(side_effect=ConnectionError("down"))
    assert await manager.release_lock("signal-x") is False
    await manager.close()


@pytest.mark.asyncio
async def test_health_uses_cached_leader_info():
    manager = DistributedLockManager()
    manager.lease_client.get = AsyncMock(side_effect=AssertionError("inline request"))
    result = await manager.health_check()
    assert result["leader_info"]["status"] == "unknown"
    await manager.close()
