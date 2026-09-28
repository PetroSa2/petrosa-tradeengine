"""LeaseClient must match data-manager's lease API (data_manager/api/routes/leases.py).

data-manager serves POST /api/v1/leases/{name}/{acquire,renew,release} and
GET /api/v1/leases/{name}; acquire/renew bodies are {"owner", "ttl_seconds"}.
A drift here makes every signal lock fail closed (no trading).
"""

from unittest.mock import AsyncMock

import pytest

from shared.distributed_lock import LeaseClient


@pytest.fixture
def client():
    c = LeaseClient(base_url="http://dm.test")
    c._client = AsyncMock()
    c._client.request = AsyncMock(return_value={"acquired": True})
    return c


@pytest.mark.asyncio
async def test_acquire_path_and_body(client):
    await client.acquire("signal_x_BTCUSDT_abc", "pod-1", 30)
    method, path = client._client.request.call_args.args
    assert (method, path) == ("POST", "/api/v1/leases/signal_x_BTCUSDT_abc/acquire")
    assert client._client.request.call_args.kwargs["json"] == {
        "owner": "pod-1",
        "ttl_seconds": 30,
    }


@pytest.mark.asyncio
async def test_renew_path_and_body(client):
    await client.renew("tradeengine-leader", "pod-1", 30)
    method, path = client._client.request.call_args.args
    assert (method, path) == ("POST", "/api/v1/leases/tradeengine-leader/renew")
    assert client._client.request.call_args.kwargs["json"] == {
        "owner": "pod-1",
        "ttl_seconds": 30,
    }


@pytest.mark.asyncio
async def test_release_and_get_paths(client):
    await client.release("tradeengine-leader", "pod-1")
    assert client._client.request.call_args.args == (
        "POST",
        "/api/v1/leases/tradeengine-leader/release",
    )
    await client.get("tradeengine-leader")
    assert client._client.request.call_args.args == (
        "GET",
        "/api/v1/leases/tradeengine-leader",
    )
