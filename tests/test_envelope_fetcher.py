"""Tests for :mod:`tradeengine.services.envelope_fetcher` (P4.6-AC3 / #421, FR62).

Covers the EnvelopeFetcher HTTP contract + cache + error classification —
matches the cio side test_envelope_fetcher.py shape so behavioral drift
between the two services would surface as a test diff.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tradeengine.services.envelope_fetcher import (
    DEFAULT_TTL_SECONDS,
    EnvelopeFetcher,
    EnvelopeFetchError,
    EnvelopeNotFoundError,
    strategy_key,
)


def _resp(status_code: int, body: Any = None, *, text: str | None = None) -> MagicMock:
    """Build a MagicMock that mimics httpx.Response for the helper's needs."""
    response = MagicMock()
    response.status_code = status_code
    response.text = (
        text if text is not None else (str(body) if body is not None else "")
    )
    if body is not None:
        response.json = MagicMock(return_value=body)
    else:
        response.json = MagicMock(side_effect=ValueError("no body"))
    return response


def _make_fetcher(handler, **kwargs) -> EnvelopeFetcher:
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=handler)
    client.aclose = AsyncMock()
    return EnvelopeFetcher(
        data_manager_url="http://data-manager:8000",
        client=client,
        **kwargs,
    )


def _operator_envelope() -> dict[str, Any]:
    return {
        "envelope_id": "env-op-1",
        "strategy_or_portfolio_key": "strategy:momentum-v3",
        "version": 5,
        "source": "operator_approved",
        "value": {"max_drawdown_pct": 7.5},
        "operator_id": "alice",
        "created_at": "2026-06-01T10:00:00Z",
    }


@pytest.mark.asyncio
async def test_get_active_returns_envelope_on_200():
    async def handler(url, timeout=None):  # noqa: ARG001
        return _resp(200, body=_operator_envelope())

    fetcher = _make_fetcher(handler)
    envelope = await fetcher.get_active("strategy:momentum-v3")
    assert envelope["version"] == 5
    assert envelope["source"] == "operator_approved"


@pytest.mark.asyncio
async def test_get_active_uses_cache_within_ttl():
    """A second call within the TTL window MUST NOT hit the network."""
    call_count = 0

    async def handler(url, timeout=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return _resp(200, body=_operator_envelope())

    fetcher = _make_fetcher(handler, ttl_seconds=60.0)
    e1 = await fetcher.get_active("strategy:momentum-v3")
    e2 = await fetcher.get_active("strategy:momentum-v3")
    assert e1 == e2
    assert call_count == 1


@pytest.mark.asyncio
async def test_get_active_raises_envelope_not_found_on_404():
    async def handler(url, timeout=None):  # noqa: ARG001
        return _resp(404)

    fetcher = _make_fetcher(handler)
    with pytest.raises(EnvelopeNotFoundError) as exc:
        await fetcher.get_active("strategy:unknown")
    assert "strategy:unknown" in str(exc.value)


@pytest.mark.asyncio
async def test_get_active_raises_envelope_fetch_error_on_5xx():
    async def handler(url, timeout=None):  # noqa: ARG001
        return _resp(503, text="Service Unavailable")

    fetcher = _make_fetcher(handler)
    with pytest.raises(EnvelopeFetchError) as exc:
        await fetcher.get_active("strategy:momentum-v3")
    assert "503" in str(exc.value)


@pytest.mark.asyncio
async def test_get_active_raises_fetch_error_on_transport_error():
    async def handler(url, timeout=None):  # noqa: ARG001
        raise httpx.ConnectError("network down")

    fetcher = _make_fetcher(handler)
    with pytest.raises(EnvelopeFetchError) as exc:
        await fetcher.get_active("strategy:momentum-v3")
    assert "transport error" in str(exc.value)


@pytest.mark.asyncio
async def test_get_active_rejects_empty_key():
    fetcher = _make_fetcher(lambda url, timeout=None: _resp(200, body={}))
    with pytest.raises(ValueError):
        await fetcher.get_active("")


@pytest.mark.asyncio
async def test_get_active_rejects_non_dict_body():
    async def handler(url, timeout=None):  # noqa: ARG001
        return _resp(200, body=["not-a-dict"])

    fetcher = _make_fetcher(handler)
    with pytest.raises(EnvelopeFetchError):
        await fetcher.get_active("strategy:momentum-v3")


def test_cache_snapshot_reports_envelope_metadata():
    """``cache_snapshot`` powers ``/healthz/envelopes`` — verify the shape."""

    async def runner():
        async def handler(url, timeout=None):  # noqa: ARG001
            return _resp(200, body=_operator_envelope())

        fetcher = _make_fetcher(handler, ttl_seconds=60.0)
        await fetcher.get_active("strategy:momentum-v3")
        snap = fetcher.cache_snapshot()
        assert "strategy:momentum-v3" in snap
        entry = snap["strategy:momentum-v3"]
        assert entry["envelope_id"] == "env-op-1"
        assert entry["version"] == 5
        assert entry["source"] == "operator_approved"
        assert entry["fresh"] is True
        assert entry["age_seconds"] >= 0

    import asyncio

    asyncio.run(runner())


def test_invalidate_clears_entry():
    async def runner():
        async def handler(url, timeout=None):  # noqa: ARG001
            return _resp(200, body=_operator_envelope())

        fetcher = _make_fetcher(handler, ttl_seconds=60.0)
        await fetcher.get_active("strategy:momentum-v3")
        assert "strategy:momentum-v3" in fetcher.cache_snapshot()
        fetcher.invalidate("strategy:momentum-v3")
        assert "strategy:momentum-v3" not in fetcher.cache_snapshot()

    import asyncio

    asyncio.run(runner())


def test_strategy_key_convention():
    """Cross-service parity: the lookup key matches the cio convention."""
    assert strategy_key("momentum-v3") == "strategy:momentum-v3"


def test_default_ttl_is_60_seconds():
    """Documented default — guard against accidental drift."""
    assert DEFAULT_TTL_SECONDS == 60.0
