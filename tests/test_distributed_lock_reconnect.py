"""Lease API behavior for the distributed lock manager."""

from unittest.mock import AsyncMock

import httpx
import pytest

from shared.distributed_lock import (
    DistributedLockManager,
    LeaseClient,
    LockState,
    LockUnavailableError,
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
