"""Regression tests for #483 — -4130 'already existing' retry reconciliation.

Verifies that BinanceFuturesExchange._execute_with_retry, when the underlying
algo-order placement raises BinanceAPIException(code=-4130), reconciles against
exchange truth (/openOrders + /openAlgoOrders) BEFORE retrying:

- AC1 / AC4: if a matching closePosition stop/TP exists in /openOrders → return
  the existing order id, no retry, increment ``already_protected`` counter.
- AC3: if it exists only in /openAlgoOrders (the most likely "phantom" source) →
  same behavior. Algo orders are NOT in /openOrders.
- If a non-matching order occupies the slot → log conflict with clientOrderId,
  increment ``conflicting_order`` counter, fall through to backoff retry.
- If nothing matches → fall through to backoff retry (existing behavior),
  terminal outcome counted as ``retry_succeeded`` or ``retry_failed``.
- Other Binance error codes are NOT affected (regression guard).
"""

# `tests/test_binance_exchange_comprehensive.py` does module-level monkey-patching
# of ``sys.modules["binance"]`` BEFORE importing the production module, which is
# what binds ``BinanceAPIException`` inside ``tradeengine.exchange.binance``. If
# two test files each install their own mock class, pytest's alphabetical
# collection order causes a race: whichever module is collected first binds its
# class to production, and the other file's tests raise a different class that
# production's ``except BinanceAPIException`` no longer matches. To avoid the
# race we import the comprehensive test module FIRST (forcing its mocks to be
# the canonical ones) and reuse its ``MockBinanceAPIException`` here. This is
# the cheapest, narrowest fix; the proper solution is to lift the mock setup
# into ``conftest.py``, but that is out of scope for this ticket.
from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

import tests.test_binance_exchange_comprehensive as _shared_mocks  # noqa: E402, F401
from tradeengine.exchange.binance import (
    BinanceAPIException,  # noqa: E402
    BinanceFuturesExchange,  # noqa: E402
)
from tradeengine.metrics import binance_4130_resolution_total  # noqa: E402


def _make_api_exc(code: int, message: str = "") -> BinanceAPIException:
    """Build a ``BinanceAPIException`` using the shared mock class signature
    (response_dict, message). ``e.code`` is force-set defensively so the test
    contract holds regardless of which mock signature actually wins the
    sys.modules race at collection time.
    """
    exc = BinanceAPIException({"code": code}, message or f"code={code}")
    exc.code = code
    return exc


def _counter_value(outcome: str, symbol: str) -> float:
    return binance_4130_resolution_total.labels(
        outcome=outcome, symbol=symbol
    )._value.get()


@pytest.fixture
def exchange():
    """BinanceFuturesExchange wired with a Mock client. Reconciliation reads from
    ``client.futures_get_open_orders`` and ``client._request_futures_api``
    (the latter is how ``get_open_algo_orders`` calls /openAlgoOrders).
    """
    ex = BinanceFuturesExchange()
    ex.client = Mock()
    ex.client.futures_get_open_orders = Mock(return_value=[])
    ex.client._request_futures_api = Mock(return_value=[])
    ex.initialized = True
    return ex


ALGO_PARAMS = {
    "symbol": "BTCUSDT",
    "side": "SELL",
    "positionSide": "LONG",
    "type": "STOP_MARKET",
    "algoType": "CONDITIONAL",
    "closePosition": True,
    "triggerPrice": "60000.0",
    "workingType": "MARK_PRICE",
    "priceProtect": True,
}


class TestAlreadyProtectedViaOpenOrders:
    """AC1 + AC4: matching closePosition stop in /openOrders → no retry, success."""

    @pytest.mark.asyncio
    async def test_returns_existing_orderid_without_retry(self, exchange):
        baseline = _counter_value("already_protected", "BTCUSDT")
        existing = {
            "orderId": 999111,
            "symbol": "BTCUSDT",
            "side": "SELL",
            "positionSide": "LONG",
            "type": "STOP_MARKET",
            "closePosition": True,
            "clientOrderId": "preexisting-sl-abc",
        }
        exchange.client.futures_get_open_orders = Mock(return_value=[existing])

        func = Mock(side_effect=_make_api_exc(-4130, "already existing"))

        result = await exchange._execute_with_retry(func, **ALGO_PARAMS)

        # No retry: func called exactly once.
        assert func.call_count == 1
        assert result["status"] == "ALREADY_EXISTS"
        assert result["orderId"] == 999111
        assert result["algoId"] == 999111
        assert result["matched_order"] is existing
        assert _counter_value("already_protected", "BTCUSDT") == baseline + 1

    @pytest.mark.asyncio
    async def test_response_shape_is_dict_so_caller_check_passes(self, exchange):
        """The four _execute_*_order callers do ``isinstance(result, dict)``.
        The synthetic ALREADY_EXISTS response must satisfy that without raising.
        """
        exchange.client.futures_get_open_orders = Mock(
            return_value=[
                {
                    "orderId": 1,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "closePosition": True,
                }
            ]
        )
        result = await exchange._execute_with_retry(
            Mock(side_effect=_make_api_exc(-4130, "already existing")),
            **ALGO_PARAMS,
        )
        assert isinstance(result, dict)


class TestAlreadyProtectedViaAlgoOrders:
    """AC3: phantom source is /openAlgoOrders; algo orders are NOT in /openOrders."""

    @pytest.mark.asyncio
    async def test_algo_endpoint_only_match(self, exchange):
        baseline = _counter_value("already_protected", "BTCUSDT")
        algo_order = {
            "algoId": 1000000095179762,
            "symbol": "BTCUSDT",
            "side": "SELL",
            "positionSide": "LONG",
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "closePosition": True,
            "clientAlgoId": "algo-xyz",
        }
        exchange.client.futures_get_open_orders = Mock(return_value=[])
        exchange.client._request_futures_api = Mock(return_value=[algo_order])

        func = Mock(side_effect=_make_api_exc(-4130, "already existing"))

        result = await exchange._execute_with_retry(func, **ALGO_PARAMS)

        assert func.call_count == 1
        assert result["algoId"] == 1000000095179762
        assert _counter_value("already_protected", "BTCUSDT") == baseline + 1
        # The algo-orders endpoint must have been consulted (AC3).
        exchange.client._request_futures_api.assert_called_with(
            "get", "openAlgoOrders", signed=True, data={"symbol": "BTCUSDT"}
        )


class TestNoMatchFallsThroughToRetry:
    @pytest.mark.asyncio
    async def test_no_match_retries_and_can_succeed(self, exchange, monkeypatch):
        """No protective order is present (truly phantom -4130). Existing
        backoff+retry path runs, the next call succeeds, counter logs
        ``retry_succeeded``.
        """

        # Patch asyncio.sleep so the test does not actually wait for backoff.
        async def _sleep_noop(_):
            return None

        monkeypatch.setattr("tradeengine.exchange.binance.asyncio.sleep", _sleep_noop)

        baseline_ok = _counter_value("retry_succeeded", "BTCUSDT")
        success = {"algoId": 42, "status": "NEW", "algoStatus": "NEW"}
        func = Mock(side_effect=[_make_api_exc(-4130, "already existing"), success])

        result = await exchange._execute_with_retry(func, **ALGO_PARAMS)

        assert func.call_count == 2
        assert result["algoId"] == 42
        assert _counter_value("retry_succeeded", "BTCUSDT") == baseline_ok + 1

    @pytest.mark.asyncio
    async def test_no_match_exhausted_retries_counts_retry_failed(
        self, exchange, monkeypatch
    ):
        async def _sleep_noop(_):
            return None

        monkeypatch.setattr("tradeengine.exchange.binance.asyncio.sleep", _sleep_noop)

        baseline_fail = _counter_value("retry_failed", "BTCUSDT")
        func = Mock(side_effect=_make_api_exc(-4130, "already existing"))

        with pytest.raises(BinanceAPIException) as excinfo:
            await exchange._execute_with_retry(func, **ALGO_PARAMS)

        assert excinfo.value.code == -4130
        assert _counter_value("retry_failed", "BTCUSDT") == baseline_fail + 1


class TestConflictingOrderDetected:
    @pytest.mark.asyncio
    async def test_wrong_position_side_logged_as_conflict(
        self, exchange, monkeypatch, caplog
    ):
        async def _sleep_noop(_):
            return None

        monkeypatch.setattr("tradeengine.exchange.binance.asyncio.sleep", _sleep_noop)

        baseline_conflict = _counter_value("conflicting_order", "BTCUSDT")
        conflicting = {
            "orderId": 5555,
            "symbol": "BTCUSDT",
            "side": "BUY",  # opposite side
            "positionSide": "SHORT",  # different hedge bucket
            "type": "STOP_MARKET",
            "closePosition": True,
            "clientOrderId": "different-leg-zzz",
        }
        exchange.client.futures_get_open_orders = Mock(return_value=[conflicting])

        # Eventually succeed on retry so the wrapper exits cleanly.
        func = Mock(
            side_effect=[
                _make_api_exc(-4130, "already existing"),
                {"algoId": 7, "status": "NEW", "algoStatus": "NEW"},
            ]
        )

        with caplog.at_level("WARNING"):
            result = await exchange._execute_with_retry(func, **ALGO_PARAMS)

        assert result["algoId"] == 7
        assert _counter_value("conflicting_order", "BTCUSDT") == baseline_conflict + 1
        assert any(
            "conflicting_order_detected" in rec.message
            and "different-leg-zzz" in rec.message
            for rec in caplog.records
        )


class TestUnrelatedErrorsUnchanged:
    """Regression: only -4130 triggers reconciliation; other codes behave as before."""

    @pytest.mark.asyncio
    async def test_non_retryable_code_still_raises_immediately(self, exchange):
        func = Mock(side_effect=_make_api_exc(-2010, "insufficient balance"))

        with pytest.raises(BinanceAPIException) as excinfo:
            await exchange._execute_with_retry(func, **ALGO_PARAMS)

        assert excinfo.value.code == -2010
        assert func.call_count == 1
        # Reconciliation must not have been consulted for a non-4130 error.
        exchange.client.futures_get_open_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_4130_on_non_algo_call_does_not_reconcile(
        self, exchange, monkeypatch
    ):
        """If closePosition is not set, this is a market/limit call and the
        reconciler must not be invoked — preserve generic retry semantics.
        """

        async def _sleep_noop(_):
            return None

        monkeypatch.setattr("tradeengine.exchange.binance.asyncio.sleep", _sleep_noop)

        func = Mock(
            side_effect=[
                _make_api_exc(-4130, "already existing"),
                {"orderId": 99, "status": "NEW"},
            ]
        )
        result = await exchange._execute_with_retry(
            func, symbol="BTCUSDT", side="BUY", type="MARKET"
        )

        assert result["orderId"] == 99
        exchange.client.futures_get_open_orders.assert_not_called()


class TestReconcilerHelperDirect:
    """Direct unit tests of _reconcile_4130_against_truth (the helper itself)."""

    @pytest.mark.asyncio
    async def test_match_in_std_orders(self, exchange):
        exchange.client.futures_get_open_orders = Mock(
            return_value=[
                {
                    "orderId": 1,
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "closePosition": True,
                }
            ]
        )
        outcome, payload = await exchange._reconcile_4130_against_truth(
            symbol="ETHUSDT",
            side="SELL",
            position_side="LONG",
            order_type="STOP_MARKET",
        )
        assert outcome == "already_protected"
        assert payload["orderId"] == 1

    @pytest.mark.asyncio
    async def test_match_in_algo_orders_with_string_closePosition(self, exchange):
        # Binance algo-order response may serialize bool as string "true".
        exchange.client.futures_get_open_orders = Mock(return_value=[])
        exchange.client._request_futures_api = Mock(
            return_value=[
                {
                    "algoId": 999,
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "algoType": "CONDITIONAL",
                    "closePosition": "true",
                }
            ]
        )
        outcome, payload = await exchange._reconcile_4130_against_truth(
            symbol="ETHUSDT",
            side="SELL",
            position_side="LONG",
            order_type="STOP_MARKET",
        )
        assert outcome == "already_protected"
        assert payload["algoId"] == 999

    @pytest.mark.asyncio
    async def test_no_orders_returns_none_found(self, exchange):
        outcome, payload = await exchange._reconcile_4130_against_truth(
            symbol="ETHUSDT",
            side="SELL",
            position_side="LONG",
            order_type="STOP_MARKET",
        )
        assert outcome == "none_found"
        assert payload is None

    @pytest.mark.asyncio
    async def test_one_way_mode_position_side_none_accepts_both(self, exchange):
        exchange.client.futures_get_open_orders = Mock(
            return_value=[
                {
                    "orderId": 1,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "BOTH",
                    "type": "STOP_MARKET",
                    "closePosition": True,
                }
            ]
        )
        outcome, _ = await exchange._reconcile_4130_against_truth(
            symbol="BTCUSDT",
            side="SELL",
            position_side=None,
            order_type="STOP_MARKET",
        )
        assert outcome == "already_protected"

    @pytest.mark.asyncio
    async def test_reduce_only_partial_close_classified_as_conflict(self, exchange):
        """Same kind + same direction but closePosition=False is a partial-close
        order that still occupies the protective slot."""
        exchange.client.futures_get_open_orders = Mock(
            return_value=[
                {
                    "orderId": 2,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "STOP_MARKET",
                    "closePosition": False,
                    "reduceOnly": True,
                    "clientOrderId": "partial-sl",
                }
            ]
        )
        outcome, payload = await exchange._reconcile_4130_against_truth(
            symbol="BTCUSDT",
            side="SELL",
            position_side="LONG",
            order_type="STOP_MARKET",
        )
        assert outcome == "conflicting_order_detected"
        assert payload["clientOrderId"] == "partial-sl"

    @pytest.mark.asyncio
    async def test_different_kind_returns_conflict(self, exchange):
        """Placing an SL while a closePosition TP already occupies the direction
        is a conflict (not already_protected): the position is missing its SL.
        """
        exchange.client.futures_get_open_orders = Mock(
            return_value=[
                {
                    "orderId": 3,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "closePosition": True,
                    "clientOrderId": "existing-tp",
                }
            ]
        )
        outcome, payload = await exchange._reconcile_4130_against_truth(
            symbol="BTCUSDT",
            side="SELL",
            position_side="LONG",
            order_type="STOP_MARKET",
        )
        assert outcome == "conflicting_order_detected"
        assert payload["clientOrderId"] == "existing-tp"
