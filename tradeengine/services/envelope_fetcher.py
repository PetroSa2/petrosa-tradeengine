"""Envelope-fetch helper for tradeengine (P4.6-AC3 / FR62 / #421).

Mirrors petrosa-cio's ``EnvelopeFetcher`` contract from
``cio/core/envelope_fetcher.py`` (shipped via petrosa-cio#154) so the two
services consume operator-approved envelopes from the same data-manager
endpoint with the same semantics — no drift.

Endpoint:
    GET ``{data_manager_url}/api/envelopes/active/{strategy_or_portfolio_key}``

The data-manager endpoint returns the highest-``version`` envelope
regardless of ``source`` (envelope-store contract from
petrosa-data-manager#188); this helper does NOT filter by source, so
operator_approved wins by construction whenever it's the latest version.

Used by the FR30 drawdown comparator (``tradeengine/risk/drawdown_enforcer.py``)
and surfaced on the ``/healthz/envelopes`` endpoint (AC3.f).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS: float = 60.0
DEFAULT_TIMEOUT_SECONDS: float = 10.0
ACTIVE_ENVELOPE_PATH = "/api/envelopes/active/"


class EnvelopeNotFoundError(LookupError):
    """Raised when data-manager has no envelope for the requested key (HTTP 404)."""


class EnvelopeFetchError(RuntimeError):
    """Raised when the fetch failed for transport-level / 5xx reasons.

    Distinct from :class:`EnvelopeNotFoundError` — callers may retry.
    """


@dataclass
class _CacheEntry:
    envelope: dict[str, Any]
    fetched_at: float


class EnvelopeFetcher:
    """TTL-cached envelope fetcher backed by data-manager's read API.

    asyncio-safe for concurrent ``get_active`` calls on the same key via a
    per-instance lock — concurrent requests coalesce into a single upstream
    call.
    """

    def __init__(
        self,
        data_manager_url: str,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = data_manager_url.rstrip("/")
        self._ttl = float(ttl_seconds)
        self._timeout = float(timeout_seconds)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def cache_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the cache state — used by ``/healthz/envelopes`` (AC3.f).

        Each entry reports the cached envelope id/version/source plus its age
        in seconds so an operator can spot stale (TTL-expired-but-not-evicted)
        entries.
        """
        now = time.monotonic()
        return {
            key: {
                "envelope_id": entry.envelope.get("envelope_id"),
                "version": entry.envelope.get("version"),
                "source": entry.envelope.get("source"),
                "age_seconds": round(now - entry.fetched_at, 3),
                "fresh": (now - entry.fetched_at) < self._ttl,
            }
            for key, entry in self._cache.items()
        }

    def invalidate(self, key: str | None = None) -> None:
        """Drop a single cache entry or (if ``key is None``) the whole cache."""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    async def get_active(self, key: str) -> dict[str, Any]:
        if not key:
            raise ValueError("envelope key must be non-empty")
        cached = self._cache.get(key)
        if cached is not None and (time.monotonic() - cached.fetched_at) < self._ttl:
            return cached.envelope
        async with self._lock:
            cached = self._cache.get(key)
            if (
                cached is not None
                and (time.monotonic() - cached.fetched_at) < self._ttl
            ):
                return cached.envelope
            envelope = await self._fetch(key)
            self._cache[key] = _CacheEntry(
                envelope=envelope, fetched_at=time.monotonic()
            )
            return envelope

    async def _fetch(self, key: str) -> dict[str, Any]:
        url = self._base_url + ACTIVE_ENVELOPE_PATH + key
        try:
            response = await self._client.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.error(
                "envelope_fetch_transport_error",
                extra={"key": key, "url": url, "error": str(exc)},
            )
            raise EnvelopeFetchError(
                f"transport error fetching envelope for {key!r}: {exc}"
            ) from exc

        if response.status_code == 404:
            logger.warning("envelope_not_found", extra={"key": key, "url": url})
            raise EnvelopeNotFoundError(f"no envelope exists for key={key!r}")
        if response.status_code >= 500:
            logger.error(
                "envelope_fetch_server_error",
                extra={
                    "key": key,
                    "status": response.status_code,
                    "body": response.text[:200],
                },
            )
            raise EnvelopeFetchError(
                f"data-manager returned HTTP {response.status_code} for {key!r}"
            )
        if response.status_code != 200:
            raise EnvelopeFetchError(
                f"data-manager returned unexpected HTTP {response.status_code} for {key!r}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise EnvelopeFetchError(
                f"data-manager returned non-JSON body for {key!r}: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise EnvelopeFetchError(
                f"data-manager returned non-object body for {key!r}: "
                f"{type(body).__name__}"
            )
        return body


# Module-level singleton — wired in tradeengine startup (api.py lifespan)
# and read by the FR30 drawdown comparator (drawdown_enforcer.py).
# ``_envelope_fetcher`` stays None when no data-manager URL is configured;
# callers fall back to legacy stub behavior in that case.
_envelope_fetcher: EnvelopeFetcher | None = None


def set_envelope_fetcher(fetcher: EnvelopeFetcher | None) -> None:
    """Inject the module-level singleton — called from app startup and tests."""
    global _envelope_fetcher
    _envelope_fetcher = fetcher


def get_envelope_fetcher() -> EnvelopeFetcher | None:
    return _envelope_fetcher


def strategy_key(strategy_id: str) -> str:
    """Build the data-manager lookup key for a strategy.

    The convention matches the cio side: ``strategy:<strategy_id>``.
    """
    return f"strategy:{strategy_id}"
