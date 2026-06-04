"""
Real HTTP behavior tests for BaseDataManagerClient (#447 Phase 0).

These tests prove:
- AC0.2: insert_one actually issues a POST to /api/v1/data/insert and
         returns an inserted_id derived from the data-manager response,
         never the literal "placeholder".
- AC0.3: on repeated 5xx, the client retries with backoff and finally
         raises a typed APIError — it does not silently succeed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from tradeengine.services.data_manager_client import (
    APIError,
    BaseDataManagerClient,
    ConnectionError as DMConnectionError,
)


def _install_transport(client: BaseDataManagerClient, handler) -> None:
    """Pre-populate the client's internal httpx.AsyncClient with a mock transport."""
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(client.timeout),
    )


@pytest.mark.asyncio
async def test_insert_one_real_http_returns_non_placeholder_id() -> None:
    """AC0.2: insert_one issues a real POST and returns a non-placeholder id."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "message": "Successfully inserted 1 records",
                "inserted_count": 1,
                "metadata": {
                    "database": "mongodb",
                    "collection": "trading_configs_audit",
                },
            },
        )

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=2)
    _install_transport(client, handler)

    record = {"_id": "abc-123", "kind": "test"}
    result = await client.insert_one("mongodb", "trading_configs_audit", record)
    await client.close()

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/data/insert")
    assert "abc-123" in captured["body"], "record payload must be transmitted verbatim"
    assert result["inserted_count"] == 1
    assert result["inserted_id"] != "placeholder", (
        "AC0.2: literal 'placeholder' is forbidden"
    )
    assert result["inserted_id"] == "abc-123", "synthetic id derives from record._id"


@pytest.mark.asyncio
async def test_insert_one_synthesises_id_when_record_has_no_identity() -> None:
    """Even with no _id/id/uuid in the record, the returned id must not be 'placeholder'."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"inserted_count": 1})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.insert_one("mongodb", "audit", {"only": "payload"})
    await client.close()

    assert result["inserted_count"] == 1
    assert result["inserted_id"], "must surface a non-empty id when count > 0"
    assert result["inserted_id"] != "placeholder"


@pytest.mark.asyncio
async def test_insert_one_zero_count_does_not_fake_success() -> None:
    """If data-manager reports inserted_count=0, the client must not invent an id."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"inserted_count": 0})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.insert_one("mongodb", "audit", {"x": 1})
    await client.close()

    assert result["inserted_count"] == 0
    assert result["inserted_id"] == "", "no count → empty id, never 'placeholder'"


@pytest.mark.asyncio
async def test_insert_one_retries_5xx_then_succeeds() -> None:
    """AC0.3 partial: bounded retry on 5xx; success on the third attempt."""

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json={"inserted_count": 1})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=3)
    _install_transport(client, handler)

    # Patch sleep to keep the test fast
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    asyncio_module = asyncio  # local alias to satisfy type-checkers
    asyncio_module.sleep = fast_sleep  # type: ignore[assignment]
    try:
        result = await client.insert_one("mongodb", "audit", {"_id": "r1"})
    finally:
        asyncio_module.sleep = real_sleep  # type: ignore[assignment]
        await client.close()

    assert attempts["n"] == 3
    assert result["inserted_count"] == 1
    assert result["inserted_id"] != "placeholder"


@pytest.mark.asyncio
async def test_insert_one_5xx_exhaustion_raises_api_error() -> None:
    """AC0.3: persistent 5xx must raise APIError after max_retries — never placeholder success."""

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(502, json={"detail": "bad gateway"})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=3)
    _install_transport(client, handler)

    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    asyncio.sleep = fast_sleep  # type: ignore[assignment]
    try:
        with pytest.raises(APIError) as excinfo:
            await client.insert_one("mongodb", "audit", {"_id": "r1"})
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]
        await client.close()

    assert attempts["n"] == 3, "must retry exactly max_retries times"
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_4xx_is_not_retried_and_raises_immediately() -> None:
    """Non-retryable client errors (4xx) must fail fast — no silent placeholder."""

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"detail": "bad request"})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=3)
    _install_transport(client, handler)

    with pytest.raises(APIError) as excinfo:
        await client.insert_one("mongodb", "audit", {"_id": "r1"})
    await client.close()

    assert attempts["n"] == 1, "4xx must not be retried"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_insert_passes_records_list_and_returns_count() -> None:
    """The `insert` path used by audit logs hits the same endpoint with records[]."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"inserted_count": 1})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.insert("mongodb", "audit", {"k": "v"})
    await client.close()

    assert '"records":' in captured["body"]
    assert '"k": "v"' in captured["body"] or '"k":"v"' in captured["body"]
    assert result == {"inserted_count": 1}


@pytest.mark.asyncio
async def test_query_returns_data_list() -> None:
    """query() unwraps the data-manager `data` array for callers."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"_id": "1", "x": 1}, {"_id": "2", "x": 2}],
                "pagination": {"total": 2},
                "metadata": {},
            },
        )

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.query("mongodb", "audit", filter={"x": 1}, limit=10)
    await client.close()

    assert len(result["data"]) == 2
    assert result["pagination"] == {"total": 2}


@pytest.mark.asyncio
async def test_upsert_one_returns_real_counts_never_placeholder() -> None:
    """upsert_one must surface real counts; upserted_id may be synthesized but never 'placeholder'."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"modified_count": 0, "upserted_count": 1},
        )

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.upsert_one("mongodb", "trading_configs_global", {}, {"x": 1})
    await client.close()

    assert result["upserted_count"] == 1
    assert result["modified_count"] == 0
    assert result["upserted_id"] != "placeholder"
    assert result["upserted_id"], "must surface a non-empty id when upsert occurred"


@pytest.mark.asyncio
async def test_delete_one_returns_real_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(200, json={"deleted_count": 1})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.delete_one("mongodb", "audit", {"x": 1})
    await client.close()

    assert result == {"deleted_count": 1}


@pytest.mark.asyncio
async def test_health_unhealthy_when_data_manager_unreachable() -> None:
    """health() must NOT raise; it must return {'status': 'unhealthy'} after retries."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=2)
    _install_transport(client, handler)

    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    asyncio.sleep = fast_sleep  # type: ignore[assignment]
    try:
        health = await client.health()
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]
        await client.close()

    assert health["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    """close() may be called multiple times safely (mongodb_client.py disconnect path)."""

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, lambda req: httpx.Response(200, json={}))
    await client.close()
    await client.close()  # second call must not raise
