"""Tests for the #972 ``oco_pair_age_seconds`` gauge.

AC3 of PetroSa2/petrosa_k8s#972:
- assert the gauge is updated on each ``_monitor_orders`` cycle
- assert the (symbol, position_side) series is removed on pair completion

The tests drive exactly one iteration of ``OCOManager._monitor_orders`` by
using a mocked exchange whose ``get_all_open_orders`` flips
``monitoring_active`` to ``False`` after the first cycle, so the ``while``
loop exits deterministically without a real 2s sleep dependency.

Run with: ``uv run pytest tests/test_oco_pair_age_metric.py -v``
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tradeengine.dispatcher import OCOManager
from tradeengine.metrics import oco_pair_age_seconds


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test-oco-pair-age-972")


@pytest.fixture(autouse=True)
def _clear_gauge() -> Any:
    """Ensure a clean gauge between tests so cross-test series don't leak."""
    oco_pair_age_seconds.clear()
    yield
    oco_pair_age_seconds.clear()


def _read_gauge(symbol: str, position_side: str) -> float | None:
    """Return the current gauge value for a label set, or None if absent."""
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(
        "petrosa_tradeengine_oco_pair_age_seconds",
        {"symbol": symbol, "position_side": position_side},
    )


def _make_oco_info(
    *, symbol: str, position_side: str, created_at: float, status: str = "active"
) -> dict[str, Any]:
    return {
        "position_id": f"{symbol}-{position_side}",
        "strategy_position_id": None,
        "entry_price": 0.0,
        "quantity": 1.0,
        "sl_order_id": "sl-1",
        "tp_order_id": "tp-1",
        "symbol": symbol,
        "position_side": position_side,
        "status": status,
        "created_at": created_at,
    }


@pytest.mark.asyncio
async def test_gauge_updated_on_monitor_cycle(logger: logging.Logger) -> None:
    """An active pair reports a positive age after one monitor cycle."""
    exch = AsyncMock()
    oco = OCOManager(exchange=exch, logger=logger)

    # Seed one active OCO pair created ~30s ago.
    created_at = time.time() - 30.0
    oco.active_oco_pairs["BTCUSDT_LONG"] = [
        _make_oco_info(symbol="BTCUSDT", position_side="LONG", created_at=created_at)
    ]
    oco.monitoring_active = True

    async def _get_all_open_orders(symbol: str) -> list[str]:
        # Both legs still open → pair remains active. Stop after this cycle.
        oco.monitoring_active = False
        return ["sl-1", "tp-1"]

    exch.get_all_open_orders = _get_all_open_orders

    await oco._monitor_orders()

    age = _read_gauge("BTCUSDT", "LONG")
    assert age is not None, "gauge series must exist for the active pair"
    # Age is time since created_at; at least the ~30s we backdated.
    assert age >= 30.0
    # Pair is still active (both legs open), so it stays tracked.
    assert oco.active_oco_pairs.get("BTCUSDT_LONG")


@pytest.mark.asyncio
async def test_gauge_removed_on_pair_completion(logger: logging.Logger) -> None:
    """When both legs vanish the pair completes and its series is removed."""
    exch = AsyncMock()
    oco = OCOManager(exchange=exch, logger=logger)

    created_at = time.time() - 10.0
    oco.active_oco_pairs["ETHUSDT_SHORT"] = [
        _make_oco_info(symbol="ETHUSDT", position_side="SHORT", created_at=created_at)
    ]
    oco.monitoring_active = True

    # Pre-seed a stale value so we can prove removal (not just absence).
    oco_pair_age_seconds.labels(symbol="ETHUSDT", position_side="SHORT").set(999.0)

    async def _get_all_open_orders(symbol: str) -> list[str]:
        # Neither leg exists → OCO completed this cycle.
        oco.monitoring_active = False
        return []

    exch.get_all_open_orders = _get_all_open_orders

    await oco._monitor_orders()

    # Completed pair: key removed from active set...
    assert "ETHUSDT_SHORT" not in oco.active_oco_pairs
    # ...and the gauge series removed (get_sample_value returns None).
    assert _read_gauge("ETHUSDT", "SHORT") is None


def test_remove_helper_is_idempotent(logger: logging.Logger) -> None:
    """_remove_oco_pair_age_series swallows KeyError for unknown series."""
    oco = OCOManager(exchange=AsyncMock(), logger=logger)
    # Never observed → remove must not raise.
    oco._remove_oco_pair_age_series("NOSUCH", "LONG")
    # Observe then remove twice → second remove must not raise.
    oco_pair_age_seconds.labels(symbol="ADAUSDT", position_side="LONG").set(1.0)
    oco._remove_oco_pair_age_series("ADAUSDT", "LONG")
    oco._remove_oco_pair_age_series("ADAUSDT", "LONG")
    assert _read_gauge("ADAUSDT", "LONG") is None
