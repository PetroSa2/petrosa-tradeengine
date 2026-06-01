"""AC3.e of P4.6 (#421, FR62): FR30 envelope wiring 4-state integration test.

Asserts ``check_and_emit`` uses the EXPECTED envelope value for each of
the four envelope-presence states the data-manager store can be in for
a given ``strategy_key``:

  - ``operator_only``: data-manager has an operator_approved envelope.
  - ``char_only``: data-manager has a characterization envelope only.
  - ``both``: data-manager has both, operator wins by highest version
    (per the envelope-store contract from petrosa-data-manager#188).
  - ``neither``: no envelope at all → fall back to legacy stub
    (``settings.max_daily_loss_pct``).

The cio-side test file (``petrosa-cio/tests/unit/test_envelope_fetcher.py``)
covers the FETCH-LAYER contract; this test covers the FR30-CONSUMER
contract — drift between the two would surface as a behavioral diff
between the cio admission decision and the tradeengine breach decision
for the same strategy_key.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.risk.drawdown_enforcer import (
    DrawdownBreachEmitter,
    check_and_emit,
)
from tradeengine.services.envelope_fetcher import (
    EnvelopeFetcher,
    set_envelope_fetcher,
)


@pytest.fixture(autouse=True)
def _reset_fetcher_singleton():
    """Each test starts with the module-level singleton cleared."""
    set_envelope_fetcher(None)
    yield
    set_envelope_fetcher(None)


def _envelope(
    source: str,
    version: int,
    max_drawdown_pct: float,
    key: str = "strategy:momentum-v3",
) -> dict[str, Any]:
    return {
        "envelope_id": f"env-{source}-{version}",
        "strategy_or_portfolio_key": key,
        "version": version,
        "source": source,
        "value": {"max_drawdown_pct": max_drawdown_pct},
        "created_at": "2026-06-01T10:00:00Z",
    }


def _resp(status_code: int, body: Any) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(body)
    response.json = MagicMock(return_value=body)
    return response


def _wire_fetcher(envelope: dict | None, status: int = 200) -> EnvelopeFetcher:
    """Build + register a fetcher with a stubbed HTTP response."""
    import httpx

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


def _emitter() -> DrawdownBreachEmitter:
    """Build a DrawdownBreachEmitter wired to a no-op AlertPublisher mock.

    The real emitter calls ``self._alert_publisher._ensure_connected()``;
    return ``None`` so the emitter's NATS-unavailable fallback path runs
    (log-only, no publish) — which is what we want for these tests since
    we only assert on the BREACH OBJECT, not the publish side-effect.
    """
    publisher = MagicMock()
    publisher._ensure_connected = AsyncMock(return_value=None)
    return DrawdownBreachEmitter(alert_publisher=publisher)


# ---------------------------------------------------------------------------
# AC3.e — 4-state integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_only_envelope_is_used_for_comparator():
    """Operator-approved is the only envelope → comparator uses its value."""
    _wire_fetcher(_envelope("operator_approved", version=3, max_drawdown_pct=8.0))
    emitter = _emitter()

    # Observed drawdown 9.0% > envelope 8.0% → breach expected
    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=9.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 8.0


@pytest.mark.asyncio
async def test_char_only_envelope_is_used_for_comparator():
    """Characterization is the only envelope → comparator uses its value."""
    _wire_fetcher(_envelope("characterization", version=2, max_drawdown_pct=12.0))
    emitter = _emitter()

    # Observed drawdown 13.0% > envelope 12.0% → breach expected
    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=13.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 12.0


@pytest.mark.asyncio
async def test_both_present_data_manager_returns_highest_version():
    """When both operator and characterization envelopes exist, the
    data-manager endpoint returns the highest-version envelope by
    contract (petrosa-data-manager#188 / #200). Operator_approved wins
    when its version is higher — verify the fetcher reads exactly what
    data-manager returns and the comparator uses that."""
    # Simulate data-manager returning operator_approved at v5 — highest
    # among operator(v5) and characterization(v3).
    _wire_fetcher(_envelope("operator_approved", version=5, max_drawdown_pct=6.0))
    emitter = _emitter()

    # Observed drawdown 7.0% > envelope 6.0% → breach expected
    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=7.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 6.0


@pytest.mark.asyncio
async def test_neither_envelope_falls_back_to_stub(monkeypatch):
    """No envelope at all → 404 from data-manager → fall back to legacy
    stub. The stub returns ``settings.max_daily_loss_pct`` as the
    comparator value."""
    monkeypatch.setattr(
        "tradeengine.risk.drawdown_enforcer.settings.max_daily_loss_pct", 4.0
    )
    _wire_fetcher(envelope=None)  # 404
    emitter = _emitter()

    # Observed drawdown 5.0% > stub 4.0% → breach expected
    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=5.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 4.0


@pytest.mark.asyncio
async def test_fetcher_unconfigured_falls_back_to_stub(monkeypatch):
    """When no fetcher is set (data-manager URL not configured), the
    helper transparently falls back to the legacy stub — guarantees
    backward compatibility during partial deploy."""
    monkeypatch.setattr(
        "tradeengine.risk.drawdown_enforcer.settings.max_daily_loss_pct", 5.0
    )
    set_envelope_fetcher(None)  # explicit
    emitter = _emitter()

    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=6.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 5.0


@pytest.mark.asyncio
async def test_envelope_with_invalid_value_falls_back_to_stub(monkeypatch):
    """Envelope returned but ``value.max_drawdown_pct`` is missing/non-numeric
    → fall back to legacy stub. The comparator must never receive a non-float."""
    monkeypatch.setattr(
        "tradeengine.risk.drawdown_enforcer.settings.max_daily_loss_pct", 3.0
    )
    bad_envelope: dict[str, Any] = {
        "envelope_id": "env-bad-1",
        "strategy_or_portfolio_key": "strategy:momentum-v3",
        "version": 1,
        "source": "operator_approved",
        "value": {"max_drawdown_pct": "not-a-number"},
        "created_at": "2026-06-01T10:00:00Z",
    }
    _wire_fetcher(bad_envelope)
    emitter = _emitter()

    # Observed 4.0% > stub 3.0% → breach expected
    breach = await check_and_emit(
        strategy_id="momentum-v3",
        observed_drawdown_pct=4.0,
        emitter=emitter,
    )
    assert breach is not None
    assert breach.envelope_value_pct == 3.0
