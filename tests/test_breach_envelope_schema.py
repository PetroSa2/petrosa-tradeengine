"""AC6.a of P4.6 / FR62 (#422): drawdown-breach event schema gains
``envelope_version`` + ``envelope_source``.

The producer-side leaf — verifies that:
  1. Breach dataclass carries the two new fields (Optional[int|str], default None)
  2. ``check_and_emit`` extracts version+source from the envelope returned by
     ``get_envelope_value_for_strategy`` and threads them through to the Breach
  3. The NATS payload emitted by ``DrawdownBreachEmitter.emit`` includes the two
     new keys (populated when present, NULL when not — backwards-compatible)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tradeengine.risk.drawdown_enforcer import (
    Breach,
    DrawdownBreachEmitter,
    check_and_emit,
    check_drawdown_breach,
)
from tradeengine.services.envelope_fetcher import (
    EnvelopeFetcher,
    set_envelope_fetcher,
)


@pytest.fixture(autouse=True)
def _reset_fetcher():
    set_envelope_fetcher(None)
    yield
    set_envelope_fetcher(None)


def _resp(status_code: int, body: Any) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(body)
    response.json = MagicMock(return_value=body)
    return response


def _envelope(source: str, version: int, max_drawdown_pct: float) -> dict[str, Any]:
    return {
        "envelope_id": f"env-{source}-{version}",
        "strategy_or_portfolio_key": "strategy:momentum-v3",
        "version": version,
        "source": source,
        "value": {"max_drawdown_pct": max_drawdown_pct},
        "created_at": "2026-06-01T10:00:00Z",
    }


def _wire_fetcher(envelope: dict | None, status: int = 200) -> EnvelopeFetcher:
    async def handler(url, timeout=None):  # noqa: ARG001
        if envelope is None:
            return _resp(404, body={"detail": "not found"})
        return _resp(status, body=envelope)

    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=handler)
    client.aclose = AsyncMock()
    fetcher = EnvelopeFetcher(
        data_manager_url="http://data-manager:8000",
        client=client,
        ttl_seconds=60.0,
    )
    set_envelope_fetcher(fetcher)
    return fetcher


class _CapturingPublisher:
    """Minimal AlertPublisher stub that captures published payloads."""

    def __init__(self) -> None:
        self._nc = self
        self.published: list[tuple[str, bytes]] = []

    async def _ensure_connected(self) -> _CapturingPublisher:
        return self

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append((subject, data))


# ---------------------------------------------------------------------------
# Schema-shape tests
# ---------------------------------------------------------------------------


def test_breach_carries_envelope_version_and_source_fields():
    breach = Breach(
        strategy_id="x",
        observed_drawdown_pct=5.0,
        envelope_value_pct=3.0,
        exceeded_by_pct=2.0,
        detected_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        envelope_version=7,
        envelope_source="operator_approved",
    )
    assert breach.envelope_version == 7
    assert breach.envelope_source == "operator_approved"


def test_breach_envelope_fields_default_to_none_for_backwards_compat():
    """Backwards-compatible event — callers that don't supply the new
    fields must still construct a Breach successfully with NULL values."""
    breach = check_drawdown_breach(
        strategy_id="x",
        observed_drawdown_pct=5.0,
        envelope_value_pct=3.0,
    )
    assert breach is not None
    assert breach.envelope_version is None
    assert breach.envelope_source is None


# ---------------------------------------------------------------------------
# check_and_emit threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_emit_threads_envelope_version_and_source_to_breach():
    """When the fetcher returns an envelope, check_and_emit MUST pluck
    its version + source and pass them through to the Breach so the
    NATS payload carries them downstream."""
    _wire_fetcher(_envelope("operator_approved", version=42, max_drawdown_pct=4.0))
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        emitter=emitter,
    )

    assert breach is not None
    assert breach.envelope_version == 42
    assert breach.envelope_source == "operator_approved"


@pytest.mark.asyncio
async def test_check_and_emit_coerces_string_version_to_int():
    """Some payload sources serialize version as string; the producer
    must coerce to int so the consumer (data-manager subscriber) gets
    a stable schema."""
    bad_envelope = _envelope("characterization", version=3, max_drawdown_pct=4.0)
    bad_envelope["version"] = "7"  # non-int but parseable
    _wire_fetcher(bad_envelope)
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_version == 7
    assert breach.envelope_source == "characterization"


@pytest.mark.asyncio
async def test_check_and_emit_leaves_fields_null_when_fallback_to_stub():
    """When the fetcher returns nothing (404 or unconfigured), the helper
    falls back to the legacy stub and the envelope_version/source MUST
    stay None — AC6.d 'tolerant of missing fields'."""
    _wire_fetcher(envelope=None)  # 404
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=8.0,
        emitter=emitter,
        envelope_value_pct=None,
    )
    assert breach is not None
    assert breach.envelope_version is None
    assert breach.envelope_source is None


@pytest.mark.asyncio
async def test_check_and_emit_handles_non_numeric_version_gracefully():
    """If the envelope payload has a non-numeric version
    (mid-deploy corruption), the producer MUST NOT crash — leaves the
    field None and continues."""
    bad_envelope = _envelope("operator_approved", version=1, max_drawdown_pct=4.0)
    bad_envelope["version"] = "not-a-number"
    _wire_fetcher(bad_envelope)
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_version is None
    # source still surfaces — only the unparseable field is dropped
    assert breach.envelope_source == "operator_approved"


# ---------------------------------------------------------------------------
# Emit payload — golden test on the NATS message shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_payload_includes_envelope_version_and_source():
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = Breach(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        envelope_value_pct=3.0,
        exceeded_by_pct=2.0,
        detected_at=__import__("datetime").datetime(
            2026, 6, 1, 10, 0, 0, tzinfo=__import__("datetime").timezone.utc
        ),
        envelope_version=11,
        envelope_source="operator_approved",
    )
    ok = await emitter.emit(breach)
    assert ok is True
    assert len(publisher.published) == 1
    subject, data = publisher.published[0]
    assert subject == "alerts.drawdown.breach.momentum-v3"
    payload = json.loads(data.decode("utf-8"))
    assert payload["envelope_version"] == 11
    assert payload["envelope_source"] == "operator_approved"


@pytest.mark.asyncio
async def test_emit_payload_carries_nulls_when_legacy_breach():
    """Legacy code path: an emitter built before #422 wired the helper
    may emit a Breach with None values — the payload must serialise
    them as JSON null, not omit them. This guarantees the data-manager
    subscriber sees a stable schema across upgrades."""
    publisher = _CapturingPublisher()
    emitter = DrawdownBreachEmitter(alert_publisher=publisher)

    breach = check_drawdown_breach(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        envelope_value_pct=3.0,
    )
    assert breach is not None
    ok = await emitter.emit(breach)
    assert ok is True
    payload = json.loads(publisher.published[0][1].decode("utf-8"))
    assert "envelope_version" in payload
    assert "envelope_source" in payload
    assert payload["envelope_version"] is None
    assert payload["envelope_source"] is None
