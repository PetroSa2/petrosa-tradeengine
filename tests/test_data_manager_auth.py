"""Tests for data-manager gateway identity propagation."""

import httpx
import pytest

from shared import data_manager_auth
from tradeengine.services.data_manager_client import BaseDataManagerClient


@pytest.mark.asyncio
async def test_data_manager_client_sets_identity_headers(monkeypatch):
    monkeypatch.setenv("DM_SERVICE_NAME", "tradeengine-test")
    monkeypatch.setenv("DM_SERVICE_TOKEN", "secret-token")

    client = BaseDataManagerClient("http://data-manager.test")
    http_client = await client._get_client()

    assert http_client.headers["X-Petrosa-Service"] == "tradeengine-test"
    assert http_client.headers["Authorization"] == "Bearer secret-token"
    await client.close()


@pytest.mark.asyncio
async def test_missing_token_sends_identity_and_logs_without_secret(
    monkeypatch, caplog
):
    monkeypatch.setenv("DM_SERVICE_NAME", "tradeengine-test")
    monkeypatch.delenv("DM_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(data_manager_auth, "_missing_token_warning_logged", False)

    client = BaseDataManagerClient("http://data-manager.test")
    http_client = await client._get_client()

    assert http_client.headers["X-Petrosa-Service"] == "tradeengine-test"
    assert "Authorization" not in http_client.headers
    assert "DM_SERVICE_TOKEN is unset" in caplog.text
    assert "secret-token" not in caplog.text
    await client.close()


@pytest.mark.asyncio
async def test_identity_headers_are_sent_on_requests(monkeypatch):
    monkeypatch.setenv("DM_SERVICE_NAME", "tradeengine-test")
    monkeypatch.setenv("DM_SERVICE_TOKEN", "secret-token")
    captured: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request.headers
        return httpx.Response(200, json={"status": "ok"})

    client = BaseDataManagerClient("http://data-manager.test", max_retries=1)
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        headers=data_manager_auth.data_manager_auth_headers(),
        transport=httpx.MockTransport(handler),
    )
    await client.health()
    await client.close()

    assert captured is not None
    assert captured["X-Petrosa-Service"] == "tradeengine-test"
    assert captured["Authorization"] == "Bearer secret-token"
