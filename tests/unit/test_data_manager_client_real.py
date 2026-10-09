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
import json
from typing import Any

import httpx
import pytest

from tradeengine.services.data_manager_client import (
    APIError,
    BaseDataManagerClient,
    CircuitOpenError,
    CircuitState,
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
async def test_query_sends_supported_filters_in_json_body() -> None:
    """Query filters must reach data-manager instead of being silently dropped."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": []})

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    await client.query(
        "mysql",
        "positions",
        filter={"status": "open"},
        sort={"entry_time": -1},
        limit=10,
    )
    await client.close()

    assert captured["body"] == {
        "database": "mysql",
        "collection": "positions",
        "filter": {"status": "open"},
        "sort": {"entry_time": -1},
        "limit": 10,
    }


@pytest.mark.asyncio
async def test_query_rejects_unsupported_keyword() -> None:
    """Unsupported query options must fail loudly rather than disappear."""

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)

    with pytest.raises(TypeError):
        query_method: Any = client.query
        await query_method(
            "mysql",
            "positions",
            params={"filter": {"status": "open"}},
        )

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [({"updated_count": 3}, 3), ({"modified_count": 2}, 2)],
)
async def test_update_one_normalizes_data_manager_count(
    response: dict[str, int], expected: int
) -> None:
    """Use data-manager's updated_count while retaining the legacy fallback."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/v1/mysql/positions"
        assert json.loads(request.content)["data"] == {"status": "closed"}
        return httpx.Response(200, json=response)

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.update_one(
        "mysql",
        "positions",
        {"position_id": "position-1"},
        {"status": "closed"},
    )
    await client.close()

    assert result["updated_count"] == expected
    assert result["modified_count"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_count", "expected_upserted"),
    [
        ({"updated_count": 1, "upserted": True}, 1, True),
        ({"updated_count": 1, "upserted": False}, 1, False),
    ],
)
async def test_upsert_one_matches_gateway_response(
    response: dict[str, object], expected_count: int, expected_upserted: bool
) -> None:
    """Map the gateway's insert/update response without inventing Mongo fields."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response,
        )

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    result = await client.upsert_one("mongodb", "trading_configs_global", {}, {"x": 1})
    await client.close()

    assert result["updated_count"] == expected_count
    assert result["upserted"] is expected_upserted
    assert result["modified_count"] is None
    assert result["upserted_count"] is None
    assert result["upserted_id"] is None


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
async def test_retryable_outage_opens_circuit_and_suppresses_requests(
    monkeypatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "down"}, request=request)

    monkeypatch.setenv("TE_DM_CIRCUIT_FAILURE_THRESHOLD", "2")
    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, handler)

    with pytest.raises(APIError):
        await client._retry_request("GET", "/health/readiness")
    with pytest.raises(APIError):
        await client._retry_request("GET", "/health/readiness")
    with pytest.raises(CircuitOpenError):
        await client._retry_request("GET", "/health/readiness")

    assert attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_circuit_half_open_probe_closes_after_recovery(monkeypatch) -> None:
    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(
        client,
        lambda request: httpx.Response(200, json={"status": "ready"}, request=request),
    )
    monkeypatch.setenv("TE_DM_CIRCUIT_RECOVERY_SECONDS", "0")
    client._circuit.state = CircuitState.OPEN
    client._circuit.opened_at = 0
    client._circuit.recovery_seconds = 0

    response = await client._retry_request("GET", "/health/readiness")

    assert response["status"] == "ready"
    assert client._circuit.state.value == "closed"
    await client.close()


@pytest.mark.asyncio
async def test_failed_half_open_probe_reopens_circuit() -> None:
    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(
        client,
        lambda request: httpx.Response(503, json={"detail": "down"}, request=request),
    )
    client._circuit.state = CircuitState.OPEN
    client._circuit.opened_at = 0
    client._circuit.recovery_seconds = 0

    with pytest.raises(APIError):
        await client._retry_request("GET", "/health/readiness")

    assert client._circuit.state is CircuitState.OPEN
    client._circuit.recovery_seconds = 30
    with pytest.raises(CircuitOpenError):
        await client._retry_request("GET", "/health/readiness")
    await client.close()


def test_invalid_circuit_environment_uses_safe_defaults(monkeypatch) -> None:
    monkeypatch.setenv("TE_DM_CIRCUIT_FAILURE_THRESHOLD", "invalid")
    monkeypatch.setenv("TE_DM_CIRCUIT_RECOVERY_SECONDS", "invalid")
    monkeypatch.setenv("TE_DM_CIRCUIT_HALF_OPEN_MAX_CALLS", "invalid")

    client = BaseDataManagerClient(base_url="http://dm.test")

    assert client._circuit.failure_threshold == 3
    assert client._circuit.recovery_seconds == 30.0
    assert client._circuit.half_open_max_calls == 1


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_open_circuit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad"}, request=request)

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=3)
    _install_transport(client, handler)

    with pytest.raises(APIError) as excinfo:
        await client._retry_request("GET", "/health/readiness")

    assert excinfo.value.status_code == 400
    assert client._circuit.state.value == "closed"
    await client.close()


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    """close() may be called multiple times safely (mongodb_client.py disconnect path)."""

    client = BaseDataManagerClient(base_url="http://dm.test", timeout=5, max_retries=1)
    _install_transport(client, lambda req: httpx.Response(200, json={}))
    await client.close()
    await client.close()  # second call must not raise
