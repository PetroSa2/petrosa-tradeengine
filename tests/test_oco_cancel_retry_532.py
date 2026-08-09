"""Unit tests for bounded-retry surviving-leg cancellation (#532, H4 of #977).

``OCOManager.cancel_other_order`` used to issue a *single* cancel call. A
transient network blip or a momentary Binance ``-1xxx`` left the surviving leg
live and dropped the OCO pair tracking — re-creating the orphan-leg incident
class this program exists to prevent.

Per tradeengine#532 the cancel is now wrapped in a **bounded retry with capped
exponential backoff**:

- Transient failures (network errors, empty responses, momentary ``-1xxx``) are
  retried within the ``OCO_CANCEL_RETRY_ATTEMPTS`` budget.
- Terminal states (``-2011`` already closed / ``-2013`` order does not exist)
  are an idempotent no-op success and are **never** retried.
- On budget exhaustion the pair remains tracked (never silently dropped), the
  ``oco_cancel_retry_exhausted_total`` counter increments, and a
  ``alerts.tradeengine.oco_cancel_retry_exhausted.<symbol>`` alert fires.

Guardrails asserted (must not reintroduce):

- #504 — no same-price cancel spin: a terminal/gone leg is not looped over.
- #490 (``-1102``) — algo cancels are not routed through ``/order``; this path
  uses ``futures_cancel_order`` for the standard SL/TP legs only.
- Idempotency — a retry after the leg actually cancelled is a no-op, not an
  error.

Related:
    - Issue: https://github.com/PetroSa2/petrosa-tradeengine/issues/532
    - Depends on (regression net): tradeengine#515 cancel-failure test.
    - Parent epic: https://github.com/PetroSa2/petrosa_k8s/issues/977
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tradeengine.dispatcher import OCOManager


class RetryFakeExchange:
    """Fake exchange whose ``futures_cancel_order`` behaviour is swappable per-test.

    Modelled on ``tests/test_oco_race_conditions.py::RaceFakeExchange`` (#515).
    """

    def __init__(self) -> None:
        self.client = Mock()
        self.cancel_calls: list[tuple[str, str]] = []
        self.client.futures_cancel_order = self._default_cancel

    def _default_cancel(self, symbol: str, orderId: str) -> dict[str, Any]:
        self.cancel_calls.append((symbol, orderId))
        return {"orderId": orderId, "status": "CANCELED"}


def _make_oco_info(
    *,
    sl_order_id: str,
    tp_order_id: str,
    symbol: str = "BTCUSDT",
    position_side: str = "LONG",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "position_id": "pos-1",
        "strategy_position_id": None,
        "entry_price": 50000.0,
        "quantity": 0.001,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "symbol": symbol,
        "position_side": position_side,
        "status": status,
        "created_at": "2026-08-09T00:00:00+00:00",
    }


@pytest.fixture
def exchange() -> RetryFakeExchange:
    return RetryFakeExchange()


@pytest.fixture
def oco_manager(exchange: RetryFakeExchange) -> OCOManager:
    logger = logging.getLogger("test_oco_cancel_retry_532")
    return OCOManager(exchange=exchange, logger=logger)


@pytest.fixture(autouse=True)
def _no_real_backoff() -> Any:
    """Patch the backoff sleep so retries do not wait real wall-clock time."""
    with patch(
        "tradeengine.dispatcher.asyncio.sleep", new=AsyncMock(return_value=None)
    ) as sleep_mock:
        yield sleep_mock


# ---------------------------------------------------------------------------
# AC1: transient failure is retried then succeeds within budget.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_error_retried_then_succeeds(
    oco_manager: OCOManager, exchange: RetryFakeExchange, _no_real_backoff: AsyncMock
) -> None:
    key = "BTCUSDT_LONG"
    oco_info = _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    oco_manager.active_oco_pairs[key] = [oco_info]

    calls = {"n": 0}

    def _flaky_then_ok(symbol: str, orderId: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("network down")
        return {"orderId": orderId, "status": "CANCELED"}

    exchange.client.futures_cancel_order = _flaky_then_ok

    # SL filled -> cancel TP.
    success, reason = await oco_manager.cancel_other_order(
        position_id="pos-1",
        filled_order_id="SL1",
        symbol="BTCUSDT",
        position_side="LONG",
    )

    assert success is True
    assert reason == "stop_loss"
    assert calls["n"] == 3  # two transient failures + one success
    # Backoff slept between the failed attempts (n-1 sleeps).
    assert _no_real_backoff.await_count == 2
    # Pair marked completed on success.
    assert oco_manager.active_oco_pairs[key][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# AC2: terminal errors are NOT retried (idempotent no-op success).
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "err_text",
    [
        "APIError(code=-2011): Unknown order sent.",
        "APIError(code=-2013): Order does not exist.",
    ],
)
async def test_terminal_error_not_retried(
    oco_manager: OCOManager,
    exchange: RetryFakeExchange,
    _no_real_backoff: AsyncMock,
    err_text: str,
) -> None:
    key = "BTCUSDT_LONG"
    oco_manager.active_oco_pairs[key] = [
        _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    ]

    calls = {"n": 0}

    def _terminal(symbol: str, orderId: str) -> dict[str, Any]:
        calls["n"] += 1
        raise Exception(err_text)

    exchange.client.futures_cancel_order = _terminal

    success, reason = await oco_manager.cancel_other_order(
        position_id="pos-1",
        filled_order_id="SL1",
        symbol="BTCUSDT",
        position_side="LONG",
    )

    # Idempotent no-op success; #504: no same-price spin on a gone leg.
    assert success is True
    assert reason == "stop_loss"
    assert calls["n"] == 1  # NOT retried
    assert _no_real_backoff.await_count == 0


# ---------------------------------------------------------------------------
# AC3: retry exhaustion keeps the pair tracked + fires metric + alert.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhaustion_keeps_pair_tracked_and_alerts(
    oco_manager: OCOManager, exchange: RetryFakeExchange, _no_real_backoff: AsyncMock
) -> None:
    key = "BTCUSDT_LONG"
    oco_info = _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    oco_manager.active_oco_pairs[key] = [oco_info]

    calls = {"n": 0}

    def _always_boom(symbol: str, orderId: str) -> dict[str, Any]:
        calls["n"] += 1
        raise ConnectionError("network down")

    exchange.client.futures_cancel_order = _always_boom

    with (
        patch("tradeengine.dispatcher.oco_cancel_retry_exhausted_total") as metric_mock,
        patch(
            "tradeengine.dispatcher.alert_publisher.publish",
            new=AsyncMock(return_value=True),
        ) as alert_mock,
        patch("tradeengine.dispatcher.OCO_CANCEL_RETRY_ATTEMPTS", 3),
    ):
        success, reason = await oco_manager.cancel_other_order(
            position_id="pos-1",
            filled_order_id="SL1",
            symbol="BTCUSDT",
            position_side="LONG",
        )

    assert success is False
    assert reason == "stop_loss"
    # All attempts exhausted.
    assert calls["n"] == 3
    # Pair NOT dropped — remains tracked and still active for the next poll.
    assert key in oco_manager.active_oco_pairs
    assert oco_manager.active_oco_pairs[key][0] is oco_info
    assert oco_manager.active_oco_pairs[key][0]["status"] == "active"
    # Failure metric incremented once with the last error class.
    metric_mock.labels.assert_called_once_with(
        symbol="BTCUSDT", reason="ConnectionError"
    )
    metric_mock.labels.return_value.inc.assert_called_once()
    # Critical alert fired on the oco_cancel_retry_exhausted.<symbol> subject.
    alert_mock.assert_awaited_once()
    kwargs = alert_mock.await_args.kwargs
    assert kwargs["alert_name"] == "oco_cancel_retry_exhausted.BTCUSDT"
    assert kwargs["severity"] == "critical"
    assert kwargs["payload"]["attempts"] == 3
    assert kwargs["payload"]["last_error"] == "ConnectionError"


# ---------------------------------------------------------------------------
# AC4 (#490 regression): algo cancels are NOT routed through /order.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_uses_futures_cancel_order_not_algo_order_path(
    oco_manager: OCOManager, exchange: RetryFakeExchange, _no_real_backoff: AsyncMock
) -> None:
    """#490 (-1102): the surviving-leg cancel must go through
    ``futures_cancel_order`` (standard order path), never the raw ``/order``
    algo route. Also asserts #504: exactly one cancel per successful attempt
    (no same-price spin)."""
    key = "BTCUSDT_LONG"
    oco_manager.active_oco_pairs[key] = [
        _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    ]

    # _request_futures_api is the raw algo-order route; it must NOT be called.
    exchange.client._request_futures_api = Mock(
        side_effect=AssertionError("algo /order route used for cancel (#490)")
    )

    success, _ = await oco_manager.cancel_other_order(
        position_id="pos-1",
        filled_order_id="SL1",
        symbol="BTCUSDT",
        position_side="LONG",
    )

    assert success is True
    # Exactly one cancel via the standard path (#504: no spin).
    assert exchange.cancel_calls == [("BTCUSDT", "TP1")]
    exchange.client._request_futures_api.assert_not_called()


# ---------------------------------------------------------------------------
# AC5 (rollback lever): OCO_CANCEL_RETRY_ATTEMPTS=1 == single-attempt behaviour.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_attempt_when_retry_disabled(
    oco_manager: OCOManager, exchange: RetryFakeExchange, _no_real_backoff: AsyncMock
) -> None:
    key = "BTCUSDT_LONG"
    oco_manager.active_oco_pairs[key] = [
        _make_oco_info(sl_order_id="SL1", tp_order_id="TP1")
    ]

    calls = {"n": 0}

    def _boom(symbol: str, orderId: str) -> dict[str, Any]:
        calls["n"] += 1
        raise ConnectionError("network down")

    exchange.client.futures_cancel_order = _boom

    with (
        patch("tradeengine.dispatcher.OCO_CANCEL_RETRY_ATTEMPTS", 1),
        patch(
            "tradeengine.dispatcher.alert_publisher.publish",
            new=AsyncMock(return_value=True),
        ),
    ):
        success, _ = await oco_manager.cancel_other_order(
            position_id="pos-1",
            filled_order_id="SL1",
            symbol="BTCUSDT",
            position_side="LONG",
        )

    assert success is False
    assert calls["n"] == 1  # single attempt, no backoff sleeps
    assert _no_real_backoff.await_count == 0
