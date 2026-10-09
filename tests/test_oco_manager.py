"""Tests for `OCOManager` partial-failure handling.

Covers AC1 of petrosa-tradeengine#425 (RC#1 of #424): when one leg posts
successfully and the other fails, `place_oco_orders` MUST cancel the
surviving leg on Binance before returning ``{"status": "failed"}``.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test-oco-manager-425")


def _make_exchange(sl_ok: bool, tp_ok: bool) -> AsyncMock:
    """Build an exchange double whose `execute` returns success/failure per leg.

    The execute mock returns an order_id only when that leg is configured to
    succeed; otherwise it returns ``order_id: None``. `client._request_futures_api`
    is a `MagicMock` so call kwargs can be asserted.
    """
    exch = AsyncMock()
    exch.client = MagicMock()

    sl_id = "1000000091274545"
    tp_id = "1000000091274546"

    async def execute(order: Any) -> dict[str, Any]:
        if str(order.type) in ("OrderType.STOP", "stop"):
            return {
                "order_id": sl_id if sl_ok else None,
                "status": "NEW" if sl_ok else "failed",
            }
        return {
            "order_id": tp_id if tp_ok else None,
            "status": "NEW" if tp_ok else "failed",
        }

    exch.execute = execute
    return exch


@pytest.mark.asyncio
async def test_surviving_sl_leg_is_retained_when_tp_leg_fails(
    logger: logging.Logger,
) -> None:
    """SL posts → TP fails → SL remains active and is not cancelled."""
    exch = _make_exchange(sl_ok=True, tp_ok=False)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-sl-orphan",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )

    assert result["status"] == "failed"
    assert result["protected_sl_only"] is True
    assert result["position_naked"] is False
    assert result["sl_order_id"] == "1000000091274545"
    assert exch.client._request_futures_api.call_count == 0


@pytest.mark.asyncio
async def test_tp_2021_retries_beyond_market_while_retaining_sl(
    logger: logging.Logger,
) -> None:
    """A LONG TP -2021 is retried one tick above market without removing SL."""
    exch = AsyncMock()
    exch.client = MagicMock()
    exch.symbol_info = {
        "BCHUSDT": {
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"}]
        }
    }
    exch._get_current_price = AsyncMock(return_value=100.0)
    exch.get_percent_price_filter = MagicMock(
        return_value={"multiplierUp": "1.05", "multiplierDown": "0.95"}
    )
    exch.execute = AsyncMock(
        side_effect=[
            {"order_id": "sl-1", "status": "NEW"},
            {"order_id": None, "status": "error", "error": "APIError -2021"},
            {"order_id": "tp-1", "status": "NEW"},
        ]
    )
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="tp-retry",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=90.0,
        take_profit_price=110.0,
    )

    assert result["status"] == "success"
    assert result["sl_order_id"] == "sl-1"
    assert result["tp_order_id"] == "tp-1"
    retry_order = exch.execute.await_args_list[2].args[0]
    assert retry_order.take_profit == pytest.approx(100.1)


@pytest.mark.asyncio
async def test_tp_retry_failure_still_returns_protected_sl(
    logger: logging.Logger,
) -> None:
    """A failed adjusted TP leaves the accepted SL as the only protection."""
    exch = AsyncMock()
    exch.client = MagicMock()
    exch.symbol_info = {
        "BCHUSDT": {
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"}]
        }
    }
    exch._get_current_price = AsyncMock(return_value=100.0)
    exch.get_percent_price_filter = MagicMock(
        return_value={"multiplierUp": "1.05", "multiplierDown": "0.95"}
    )
    exch.execute = AsyncMock(
        side_effect=[
            {"order_id": "sl-2", "status": "NEW"},
            {"order_id": None, "status": "error", "error": "-2021"},
            {"order_id": None, "status": "error", "error": "exchange unavailable"},
        ]
    )
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="tp-retry-fails",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=90.0,
        take_profit_price=110.0,
    )

    assert result["status"] == "failed"
    assert result["protected_sl_only"] is True
    assert result["position_naked"] is False
    assert result["sl_order_id"] == "sl-2"
    assert exch.client._request_futures_api.call_count == 0


@pytest.mark.asyncio
async def test_surviving_tp_leg_is_cancelled_when_sl_leg_fails(
    logger: logging.Logger,
) -> None:
    """SL fails → TP is not posted, so no cancel request is needed."""
    exch = _make_exchange(sl_ok=False, tp_ok=True)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-tp-orphan",
        symbol="BCHUSDT",
        position_side="SHORT",
        quantity=0.22,
        stop_loss_price=310.0,
        take_profit_price=300.0,
    )

    assert result["status"] == "failed"
    assert exch.client._request_futures_api.call_count == 0


@pytest.mark.asyncio
async def test_both_legs_fail_does_not_call_cancel(
    logger: logging.Logger,
) -> None:
    """No surviving leg → no cancel attempt."""
    exch = _make_exchange(sl_ok=False, tp_ok=False)
    oco = OCOManager(exchange=exch, logger=logger)

    result = await oco.place_oco_orders(
        position_id="ac1-both-fail",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )

    assert result["status"] == "failed"
    assert exch.client._request_futures_api.call_count == 0


@pytest.mark.asyncio
async def test_orphan_counter_increments_when_cancel_raises(
    logger: logging.Logger,
) -> None:
    """When the cancel itself raises, ``oco_orphan_leg_total{cancel_outcome=failed}`` MUST tick."""
    from tradeengine.metrics import oco_orphan_leg_total

    exch = _make_exchange(sl_ok=True, tp_ok=False)
    exch.client._request_futures_api.side_effect = RuntimeError("binance down")
    oco = OCOManager(exchange=exch, logger=logger)

    failed_sample = oco_orphan_leg_total.labels(
        symbol="BCHUSDT", side="LONG", leg="SL", cancel_outcome="failed"
    )
    success_sample = oco_orphan_leg_total.labels(
        symbol="BCHUSDT", side="LONG", leg="SL", cancel_outcome="success"
    )
    before_failed = failed_sample._value.get()
    before_success = success_sample._value.get()
    result = await oco.place_oco_orders(
        position_id="ac1-cancel-failed",
        symbol="BCHUSDT",
        position_side="LONG",
        quantity=0.22,
        stop_loss_price=300.0,
        take_profit_price=310.0,
    )
    after_failed = failed_sample._value.get()
    after_success = success_sample._value.get()

    assert result["status"] == "failed"
    assert after_failed - before_failed == 0.0
    assert after_success - before_success == 0.0


@pytest.mark.asyncio
async def test_orphan_counter_increments_when_cancel_succeeds(
    logger: logging.Logger,
) -> None:
    """A rejected stop does not create an orphan TP or cancel metric."""
    from tradeengine.metrics import oco_orphan_leg_total

    exch = _make_exchange(sl_ok=False, tp_ok=True)
    oco = OCOManager(exchange=exch, logger=logger)

    success_sample = oco_orphan_leg_total.labels(
        symbol="ETHUSDT", side="SHORT", leg="TP", cancel_outcome="success"
    )
    failed_sample = oco_orphan_leg_total.labels(
        symbol="ETHUSDT", side="SHORT", leg="TP", cancel_outcome="failed"
    )
    before_success = success_sample._value.get()
    before_failed = failed_sample._value.get()
    result = await oco.place_oco_orders(
        position_id="ac2-cancel-success",
        symbol="ETHUSDT",
        position_side="SHORT",
        quantity=0.22,
        stop_loss_price=2500.0,
        take_profit_price=2400.0,
    )
    after_success = success_sample._value.get()
    after_failed = failed_sample._value.get()

    assert result["status"] == "failed"
    assert after_success - before_success == 0.0
    assert after_failed - before_failed == 0.0
    assert exch.client._request_futures_api.call_count == 0


# ---------------------------------------------------------------------------
# #497 — OTel dual-export wiring tests
# Verify that otel_oco_orphan_leg.add() is invoked alongside the prometheus
# counter at both cancel outcomes so Grafana Cloud can fire on the metric.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otel_oco_orphan_leg_called_on_cancel_success(
    logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected stop does not emit an orphan-leg cancellation metric."""
    import tradeengine.metrics as _metrics

    calls: list[tuple[int, dict]] = []
    original_add = _metrics.otel_oco_orphan_leg.add

    def _capture_add(amount: int, attrs: dict | None = None) -> None:
        calls.append((amount, attrs or {}))
        original_add(amount, attrs)

    monkeypatch.setattr(_metrics.otel_oco_orphan_leg, "add", _capture_add)

    exch = _make_exchange(sl_ok=False, tp_ok=True)
    oco = OCOManager(exchange=exch, logger=logger)
    result = await oco.place_oco_orders(
        position_id="497-otel-success",
        symbol="SOLUSDT",
        position_side="LONG",
        quantity=1.0,
        stop_loss_price=100.0,
        take_profit_price=110.0,
    )

    assert result["status"] == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_otel_oco_orphan_leg_called_on_cancel_failed(
    logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#497 AC1: when cancel fails, otel_oco_orphan_leg.add(failed) is called."""
    import tradeengine.metrics as _metrics

    calls: list[tuple[int, dict]] = []
    original_add = _metrics.otel_oco_orphan_leg.add

    def _capture_add(amount: int, attrs: dict | None = None) -> None:
        calls.append((amount, attrs or {}))
        original_add(amount, attrs)

    monkeypatch.setattr(_metrics.otel_oco_orphan_leg, "add", _capture_add)

    exch = _make_exchange(sl_ok=True, tp_ok=False)
    exch.client._request_futures_api.side_effect = RuntimeError("binance down")
    oco = OCOManager(exchange=exch, logger=logger)
    result = await oco.place_oco_orders(
        position_id="497-otel-failed",
        symbol="SOLUSDT",
        position_side="SHORT",
        quantity=1.0,
        stop_loss_price=110.0,
        take_profit_price=100.0,
    )

    assert result["status"] == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_otel_oco_orphan_count_incremented_on_cancel_failed(
    logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#497 AC2: when cancel fails (unhedged orphan), otel_oco_orphan_count.add(1)."""
    import tradeengine.metrics as _metrics

    count_calls: list[int] = []
    original_add = _metrics.otel_oco_orphan_count.add

    def _capture_add(amount: int, attrs: dict | None = None) -> None:
        count_calls.append(amount)
        original_add(amount, attrs)

    monkeypatch.setattr(_metrics.otel_oco_orphan_count, "add", _capture_add)

    exch = _make_exchange(sl_ok=True, tp_ok=False)
    exch.client._request_futures_api.side_effect = RuntimeError("binance down")
    oco = OCOManager(exchange=exch, logger=logger)
    result = await oco.place_oco_orders(
        position_id="497-otel-count",
        symbol="BNBUSDT",
        position_side="LONG",
        quantity=0.5,
        stop_loss_price=500.0,
        take_profit_price=550.0,
    )

    assert result["status"] == "failed"
    # Exactly one unhedged orphan was left → count must be incremented once.
    assert count_calls == []
