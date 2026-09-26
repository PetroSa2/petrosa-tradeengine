"""Regression tests for PetroSa2/petrosa-tradeengine#650.

The OCO surviving-leg cancel used the standard-order endpoint
(``futures_cancel_order`` -> ``DELETE /fapi/v1/order``) for protective legs
that are ALGO orders (``POST /fapi/v1/algoOrder``). Binance answered ``-2011
Unknown order sent``, the retry loop read that as "already gone", marked the
pair completed and left the sibling SL live as an orphan (DOTUSDT,
2026-09-26 15:17Z). The completion handler then logged ``No strategy_position_id
in OCO info`` for the algo-keyed fill of a pair rebuilt after a restart and
returned before its own paired-leg cancel.

The tests drive a REAL ``BinanceFuturesExchange`` over an in-memory Binance
fake whose standard ``/order`` endpoint knows nothing about algo orders, just
like the exchange.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.binance_futures_fake import (
    FakeFuturesClient,
    api_error,
    make_exchange,
    pin_binance_module,
)
from tradeengine.dispatcher import OCOManager
from tradeengine.strategy_position_manager import strategy_position_manager

SYMBOL = "DOTUSDT"


@pytest.fixture(autouse=True)
def _explicit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "explicit_qty")
    pin_binance_module(monkeypatch)


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("tradeengine.dispatcher.asyncio.sleep", new=AsyncMock()):
        yield


@pytest.fixture
def client() -> FakeFuturesClient:
    c = FakeFuturesClient()
    c.set_position(SYMBOL, "LONG", 82.8)
    return c


@pytest.fixture
def oco(client: FakeFuturesClient) -> OCOManager:
    manager = OCOManager(
        exchange=make_exchange(client),
        logger=logging.getLogger("test-650"),
        dispatcher=SimpleNamespace(
            protective_leg_manager=None,
            position_manager=None,
            strategy_position_to_position={},
        ),
    )
    manager.start_monitoring = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.fixture(autouse=True)
def clean_strategy_positions() -> Any:
    saved = dict(strategy_position_manager.strategy_positions)
    strategy_position_manager.strategy_positions.clear()
    yield strategy_position_manager.strategy_positions
    strategy_position_manager.strategy_positions.clear()
    strategy_position_manager.strategy_positions.update(saved)


async def _place_pair(oco: OCOManager, **kw: Any) -> dict[str, Any]:
    result = await oco.place_oco_orders(
        position_id=kw.get("position_id", "pos-dot"),
        symbol=SYMBOL,
        position_side="LONG",
        quantity=82.8,
        stop_loss_price=3.6,
        take_profit_price=4.4,
        strategy_position_id=kw.get("strategy_position_id"),
        entry_price=4.0,
    )
    assert result["status"] == "success", result
    return result


# ---------------------------------------------------------------------------
# AC1: TP algo leg fills -> SL algo leg cancelled via cancel_algo_order.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac1_tp_fill_cancels_sl_via_cancel_algo_order(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    result = await _place_pair(oco)
    sl_id, tp_id = result["sl_order_id"], result["tp_order_id"]
    pair = oco.active_oco_pairs[f"{SYMBOL}_LONG"][0]
    assert pair["sl_is_algo"] is True and pair["tp_is_algo"] is True

    client.fill_leg(tp_id, 0.0)  # the TP triggered and closed the side
    await oco._monitor_iteration()

    assert client.open_legs(SYMBOL, "LONG") == [], "sibling SL left orphaned"
    assert {"symbol": SYMBOL, "algoId": sl_id} in client.deletes()
    assert client.standard_cancels == [], "futures_cancel_order must not be used"
    assert pair["status"] == "completed"


@pytest.mark.asyncio
async def test_ac1_direct_cancel_other_order_uses_algo_endpoint(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    result = await _place_pair(oco)
    oco.exchange.cancel_algo_order = AsyncMock(  # type: ignore[method-assign]
        wraps=oco.exchange.cancel_algo_order
    )

    ok, reason = await oco.cancel_other_order(
        "pos-dot", result["tp_order_id"], symbol=SYMBOL, position_side="LONG"
    )

    assert (ok, reason) == (True, "take_profit")
    oco.exchange.cancel_algo_order.assert_awaited_once_with(
        SYMBOL, result["sl_order_id"]
    )
    assert client.standard_cancels == []


# ---------------------------------------------------------------------------
# AC2: a standard (non-algo) leg is still cancelled via futures_cancel_order.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac2_standard_leg_uses_futures_cancel_order(oco: OCOManager) -> None:
    calls: list[tuple[str, str]] = []

    def _std_cancel(symbol: str, orderId: str) -> dict[str, Any]:
        calls.append((symbol, orderId))
        return {"orderId": orderId, "status": "CANCELED"}

    oco.exchange.client.futures_cancel_order = _std_cancel  # type: ignore[union-attr]
    oco.exchange.cancel_algo_order = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("algo endpoint used for a standard leg")
    )
    oco.active_oco_pairs["BTCUSDT_LONG"] = [
        {
            "position_id": "p1",
            "sl_order_id": "111",
            "tp_order_id": "222",
            "sl_is_algo": False,
            "tp_is_algo": False,
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "status": "active",
        }
    ]

    ok, reason = await oco.cancel_other_order(
        "p1", "111", symbol="BTCUSDT", position_side="LONG"
    )

    assert (ok, reason) == (True, "stop_loss")
    assert calls == [("BTCUSDT", "222")]
    oco.exchange.cancel_algo_order.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC3: -2011 from cancel_algo_order is an idempotent success; the wrong
# endpoint is never consulted.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac3_2011_from_algo_endpoint_is_idempotent_success(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    result = await _place_pair(oco)
    sl_id = result["sl_order_id"]
    client.algo_orders.pop(sl_id)  # the SL is genuinely gone (e.g. both fired)

    ok, reason = await oco.cancel_other_order(
        "pos-dot", result["tp_order_id"], symbol=SYMBOL, position_side="LONG"
    )

    assert (ok, reason) == (True, "take_profit")
    # exactly one algo DELETE (not retried) and the /order endpoint never asked
    assert client.deletes() == [{"symbol": SYMBOL, "algoId": sl_id}]
    assert client.standard_cancels == []
    pair = oco.active_oco_pairs[f"{SYMBOL}_LONG"][0]
    assert pair["status"] == "externally_closed"
    assert pair["surviving_leg_gone"] is True


@pytest.mark.asyncio
async def test_ac3_standard_endpoint_2011_cannot_mask_live_algo_leg(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    """The exact DOTUSDT failure: /order says -2011 while the algo SL is live.
    With routing by leg kind the /order endpoint is never reached."""
    result = await _place_pair(oco)
    await oco.cancel_other_order(
        "pos-dot", result["tp_order_id"], symbol=SYMBOL, position_side="LONG"
    )
    assert result["sl_order_id"] not in client.algo_orders
    assert client.standard_cancels == []


@pytest.mark.asyncio
async def test_transient_algo_cancel_error_is_retried(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    result = await _place_pair(oco)
    client.cancel_errors[result["sl_order_id"]] = api_error(-1001, "disconnected")

    ok, _ = await oco.cancel_other_order(
        "pos-dot", result["tp_order_id"], symbol=SYMBOL, position_side="LONG"
    )

    assert ok is True
    assert len(client.deletes()) == 2  # transient failure + success
    assert result["sl_order_id"] not in client.algo_orders


@pytest.mark.asyncio
async def test_missing_leg_kind_flag_defaults_to_algo(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    """A pair recorded before #650 has no *_is_algo flag: every protective leg
    since #352 is an algo order, so it routes to the algo endpoint."""
    sl_id = client.add_algo_order(
        symbol=SYMBOL, orderType="STOP_MARKET", quantity="82.8", triggerPrice="3.6"
    )
    oco.active_oco_pairs[f"{SYMBOL}_LONG"] = [
        {
            "position_id": "legacy",
            "sl_order_id": sl_id,
            "tp_order_id": "999",
            "symbol": SYMBOL,
            "position_side": "LONG",
            "status": "active",
        }
    ]

    ok, _ = await oco.cancel_other_order(
        "legacy", "999", symbol=SYMBOL, position_side="LONG"
    )

    assert ok is True
    assert sl_id not in client.algo_orders
    assert client.standard_cancels == []


# ---------------------------------------------------------------------------
# AC4: an algo-keyed fill id resolves its OCO info / strategy position.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac4_reconciled_pair_resolves_strategy_by_filled_algo_id(
    oco: OCOManager,
    client: FakeFuturesClient,
    clean_strategy_positions: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sl_id = client.add_algo_order(
        symbol=SYMBOL,
        orderType="STOP_MARKET",
        quantity="82.8",
        triggerPrice="3.600",
        closePosition=True,
    )
    tp_id = client.add_algo_order(
        symbol=SYMBOL,
        orderType="TAKE_PROFIT_MARKET",
        quantity="82.8",
        triggerPrice="4.400",
        closePosition=True,
    )
    # A pod restart rebuilds the pair from openAlgoOrders: no strategy id.
    assert await oco.reconcile_from_exchange() == 1
    pair = oco.active_oco_pairs[f"{SYMBOL}_LONG"][0]
    assert pair["strategy_position_id"] is None
    assert pair["sl_is_algo"] is True and pair["tp_is_algo"] is True

    clean_strategy_positions["spid-dot"] = {
        "strategy_position_id": "spid-dot",
        "strategy_id": "momentum",
        "symbol": SYMBOL,
        "side": "LONG",
        "entry_price": 4.0,
        "entry_quantity": 82.8,
        "status": "open",
        "exchange_position_key": f"{SYMBOL}_LONG",
        "sl_order_id": sl_id,
        "tp_order_id": tp_id,
    }
    close_mock = AsyncMock(return_value={"decision_id": "d1"})
    with (
        patch.object(strategy_position_manager, "close_strategy_position", close_mock),
        caplog.at_level(logging.INFO),
    ):
        client.fill_leg(tp_id, 0.0)
        await oco._monitor_iteration()

    close_mock.assert_awaited_once()
    assert close_mock.await_args.kwargs["strategy_position_id"] == "spid-dot"
    assert close_mock.await_args.kwargs["close_reason"] == "take_profit"
    assert "No strategy_position_id" not in caplog.text
    assert sl_id not in client.algo_orders  # sibling cancelled via algo endpoint
    assert client.standard_cancels == []


@pytest.mark.asyncio
async def test_ac4_resolves_single_open_strategy_position_on_side(
    oco: OCOManager, clean_strategy_positions: dict[str, Any]
) -> None:
    clean_strategy_positions["only"] = {
        "status": "open",
        "exchange_position_key": f"{SYMBOL}_LONG",
    }
    clean_strategy_positions["other-side"] = {
        "status": "open",
        "exchange_position_key": f"{SYMBOL}_SHORT",
    }
    pair = {"symbol": SYMBOL, "position_side": "LONG", "sl_order_id": "1"}
    assert oco._resolve_strategy_position_id(pair, "2") == "only"


@pytest.mark.asyncio
async def test_ac4_resolves_via_position_mapping(
    oco: OCOManager, clean_strategy_positions: dict[str, Any]
) -> None:
    clean_strategy_positions["a"] = {"status": "open", "exchange_position_key": "X"}
    clean_strategy_positions["b"] = {"status": "open", "exchange_position_key": "X"}
    oco.dispatcher.strategy_position_to_position = {"b": "durable-1"}
    pair = {"symbol": "X", "position_side": "LONG", "position_id": "durable-1"}
    assert oco._resolve_strategy_position_id(pair, "9") == "b"


@pytest.mark.asyncio
async def test_ac4_ambiguous_side_is_not_guessed(
    oco: OCOManager, clean_strategy_positions: dict[str, Any]
) -> None:
    for spid in ("a", "b"):
        clean_strategy_positions[spid] = {
            "status": "open",
            "exchange_position_key": f"{SYMBOL}_LONG",
        }
    pair = {"symbol": SYMBOL, "position_side": "LONG"}
    assert oco._resolve_strategy_position_id(pair, "9") is None


@pytest.mark.asyncio
async def test_unattributable_fill_still_cancels_paired_leg(
    oco: OCOManager,
    client: FakeFuturesClient,
    clean_strategy_positions: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The old handler returned BEFORE its paired-leg cancel when it could not
    attribute the fill. Cleanup must run regardless."""
    sl_id = client.add_algo_order(
        symbol=SYMBOL, orderType="STOP_MARKET", quantity="82.8", triggerPrice="3.6"
    )
    pair = {
        "position_id": "reconciled_x",
        "strategy_position_id": None,
        "sl_order_id": sl_id,
        "tp_order_id": "777",
        "sl_is_algo": True,
        "tp_is_algo": True,
        "symbol": SYMBOL,
        "position_side": "LONG",
        "status": "active",
    }
    with caplog.at_level(logging.WARNING):
        await oco._close_position_on_oco_completion(
            position_id="reconciled_x",
            filled_order_id="777",
            close_reason="take_profit",
            oco_info=pair,
            dispatcher=None,
        )

    assert sl_id not in client.algo_orders
    assert pair["status"] == "completed"
    assert "No strategy position attributable" in caplog.text


@pytest.mark.asyncio
async def test_completion_skips_second_cancel_when_sibling_confirmed_gone(
    oco: OCOManager,
) -> None:
    oco.exchange.cancel_algo_order = AsyncMock()  # type: ignore[method-assign]
    pair = {
        "position_id": "p",
        "strategy_position_id": None,
        "sl_order_id": "1",
        "tp_order_id": "2",
        "symbol": SYMBOL,
        "position_side": "LONG",
        "status": "active",
        "surviving_leg_gone": True,
    }
    await oco._close_position_on_oco_completion(
        position_id="p",
        filled_order_id="2",
        close_reason="take_profit",
        oco_info=pair,
        dispatcher=None,
    )
    oco.exchange.cancel_algo_order.assert_not_awaited()
    assert pair["status"] == "completed"


@pytest.mark.asyncio
async def test_completion_paired_cancel_gone_is_not_an_error(
    oco: OCOManager, caplog: pytest.LogCaptureFixture
) -> None:
    oco.exchange.cancel_algo_order = AsyncMock(  # type: ignore[method-assign]
        side_effect=api_error(-2011, "Unknown order sent.")
    )
    pair = {
        "position_id": "p",
        "sl_order_id": "1",
        "tp_order_id": "2",
        "symbol": SYMBOL,
        "position_side": "LONG",
        "status": "active",
    }
    with caplog.at_level(logging.INFO):
        await oco._close_position_on_oco_completion(
            position_id="p",
            filled_order_id="1",
            close_reason="stop_loss",
            oco_info=pair,
            dispatcher=None,
        )
    assert "already gone" in caplog.text
    assert pair["status"] == "completed"


# ---------------------------------------------------------------------------
# Leg kind is recorded from the placement response / scan source.
# ---------------------------------------------------------------------------
def test_execution_result_reports_algo_ness() -> None:
    exchange = make_exchange()
    order = SimpleNamespace(amount=1.0, model_dump=lambda: {})
    algo = exchange._format_execution_result({"algoId": 5}, order)  # type: ignore[arg-type]
    std = exchange._format_execution_result({"orderId": 6}, order)  # type: ignore[arg-type]
    synthetic_std = exchange._format_execution_result(  # type: ignore[arg-type]
        {"algoId": 7, "orderId": 7, "_is_algo_order": False}, order
    )
    assert algo["is_algo_order"] is True
    assert std["is_algo_order"] is False
    assert synthetic_std["is_algo_order"] is False


@pytest.mark.asyncio
async def test_reconcile_records_standard_legs_from_open_orders_fallback() -> None:
    exchange = make_exchange()
    exchange.get_open_algo_orders = AsyncMock(return_value=[])  # type: ignore[method-assign]
    exchange.client.futures_get_open_orders = lambda: [  # type: ignore[union-attr,assignment]
        {
            "orderId": 1,
            "symbol": "BTCUSDT",
            "type": "STOP_MARKET",
            "positionSide": "LONG",
        },
        {
            "orderId": 2,
            "symbol": "BTCUSDT",
            "type": "TAKE_PROFIT_MARKET",
            "positionSide": "LONG",
        },
    ]
    oco = OCOManager(exchange=exchange, logger=logging.getLogger("t"))
    oco.start_monitoring = AsyncMock()  # type: ignore[method-assign]
    assert await oco.reconcile_from_exchange() == 1
    pair = oco.active_oco_pairs["BTCUSDT_LONG"][0]
    assert pair["sl_is_algo"] is False and pair["tp_is_algo"] is False
