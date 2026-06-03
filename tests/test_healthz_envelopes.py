"""Tests for ``/healthz/envelopes`` (AC3.f of P4.6 / #421).

Surfaces the envelope-version-in-use per ``strategy_key`` so operators can
confirm cross-service parity with cio's matching surface. The endpoint
reflects the local EnvelopeFetcher's cache state.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from tradeengine.api import app
from tradeengine.services.envelope_fetcher import (
    EnvelopeFetcher,
    set_envelope_fetcher,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_fetcher_singleton():
    set_envelope_fetcher(None)
    yield
    set_envelope_fetcher(None)


def _resp(status_code: int, body: Any) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(body)
    response.json = MagicMock(return_value=body)
    return response


def test_healthz_envelopes_returns_unconfigured_when_no_fetcher(
    client: TestClient,
) -> None:
    """When no fetcher is wired (no DATA_MANAGER_URL effectively), the
    endpoint surfaces ``configured=false`` and an empty envelopes dict.
    Operators reading the dashboard can tell the difference between
    'unconfigured' and 'configured but cache empty'."""
    response = client.get("/healthz/envelopes")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["envelopes"] == {}
    assert "timestamp" in body


def test_healthz_envelopes_surfaces_cached_entries(client: TestClient) -> None:
    """After the fetcher has resolved a strategy_key, the endpoint
    reports envelope_id / version / source / age / freshness — matching
    the cio surface so operators can spot drift across services."""

    async def handler(url, timeout=None):  # noqa: ARG001
        return _resp(
            200,
            body={
                "envelope_id": "env-op-1",
                "strategy_or_portfolio_key": "strategy:momentum-v3",
                "version": 7,
                "source": "operator_approved",
                "value": {"max_drawdown_pct": 6.5},
                "created_at": "2026-06-01T10:00:00Z",
            },
        )

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=handler)
    mock_client.aclose = AsyncMock()

    fetcher = EnvelopeFetcher(
        data_manager_url="http://data-manager:8000",
        client=mock_client,
        ttl_seconds=60.0,
    )
    set_envelope_fetcher(fetcher)

    # Prime the cache by directly invoking the fetcher (the endpoint
    # itself does not trigger a fetch — it surfaces what's already there).
    import asyncio

    asyncio.run(fetcher.get_active("strategy:momentum-v3"))

    response = client.get("/healthz/envelopes")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert "strategy:momentum-v3" in body["envelopes"]
    entry = body["envelopes"]["strategy:momentum-v3"]
    assert entry["envelope_id"] == "env-op-1"
    assert entry["version"] == 7
    assert entry["source"] == "operator_approved"
    assert entry["fresh"] is True
    assert entry["age_seconds"] >= 0
