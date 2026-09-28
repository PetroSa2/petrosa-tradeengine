"""Leadership lost to a transient renewal failure is re-acquired by the refresh loop."""

from unittest.mock import AsyncMock

import pytest

from shared.distributed_lock import DistributedLockManager


@pytest.mark.asyncio
async def test_non_leader_reacquires_on_refresh():
    manager = DistributedLockManager()
    manager.is_leader = False
    manager.lease_client.acquire = AsyncMock(return_value={"acquired": True})
    manager.lease_client.get = AsyncMock(return_value=None)

    await manager._refresh_leadership_once()

    manager.lease_client.acquire.assert_awaited_once()
    assert manager.is_leader is True
    manager.is_leader = False  # stop the heartbeat loop started by the acquire
    await manager.close()


@pytest.mark.asyncio
async def test_leader_does_not_reacquire_on_refresh():
    manager = DistributedLockManager()
    manager.is_leader = True
    manager.lease_client.acquire = AsyncMock(return_value={"acquired": True})
    manager.lease_client.get = AsyncMock(return_value=None)

    await manager._refresh_leadership_once()

    manager.lease_client.acquire.assert_not_awaited()
    manager.is_leader = False
    await manager.close()


@pytest.mark.asyncio
async def test_reacquire_failure_keeps_non_leader():
    manager = DistributedLockManager()
    manager.is_leader = False
    manager.lease_client.acquire = AsyncMock(side_effect=ConnectionError("down"))
    manager.lease_client.get = AsyncMock(return_value=None)

    await manager._refresh_leadership_once()

    assert manager.is_leader is False
    await manager.close()
