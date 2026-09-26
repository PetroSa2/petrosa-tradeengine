"""Tests for PetroSa2/petrosa-tradeengine#651 — explicit-quantity protective legs.

On the Binance testnet a triggered ``closePosition=true`` SL/TP executed with
"current position + a hidden per-side residual", leaving sign-inverted
hedge-mode positions (LTC/DOT/BTC/XRP, #566). Legs are now placed with an
explicit quantity equal to the side position and kept converged by
``ProtectiveLegManager``; ``TE_PROTECTIVE_LEG_MODE=close_position`` restores
the old requests byte-for-byte.

A REAL ``BinanceFuturesExchange`` runs over the in-memory Binance fake in
``tests/binance_futures_fake.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from tests.binance_futures_fake import (
    FakeFuturesClient,
    api_error,
    make_exchange,
    pin_binance_module,
)
from tradeengine.dispatcher import OCOManager
from tradeengine.metrics import (
    protective_fill_inversion_total,
    protective_leg_sync_actions_total,
)
from tradeengine.protective_leg_mode import protective_leg_mode
from tradeengine.protective_legs import (
    ProtectiveLeg,
    ProtectiveLegManager,
    is_gone_error,
    is_inverted,
    leg_kind_of,
    side_legs,
)
from tradeengine.services.alert_publisher import alert_publisher

DOT = "DOTUSDT"
OLD_MS = time.time() * 1000 - 10 * 60 * 1000  # a leg placed 10 minutes ago


@pytest.fixture(autouse=True)
def _explicit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "explicit_qty")
    pin_binance_module(monkeypatch)


@pytest.fixture(autouse=True)
def _isolated_strategy_positions() -> Any:
    from tradeengine.strategy_position_manager import strategy_position_manager

    saved = dict(strategy_position_manager.strategy_positions)
    strategy_position_manager.strategy_positions.clear()
    yield
    strategy_position_manager.strategy_positions.clear()
    strategy_position_manager.strategy_positions.update(saved)


@pytest.fixture
def client() -> FakeFuturesClient:
    return FakeFuturesClient()


@pytest.fixture
def exchange(client: FakeFuturesClient) -> Any:
    return make_exchange(client)


@pytest.fixture
def oco(exchange: Any) -> OCOManager:
    manager = OCOManager(
        exchange=exchange,
        logger=logging.getLogger("test-651"),
        dispatcher=SimpleNamespace(
            protective_leg_manager=None,
            position_manager=None,
            strategy_position_to_position={},
        ),
    )
    manager.start_monitoring = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.fixture
def manager(exchange: Any, oco: OCOManager) -> ProtectiveLegManager:
    mgr = ProtectiveLegManager(
        exchange,
        oco,
        sync_interval_sec=3600,
        flat_grace_sec=30,
        migrate_legacy=True,
        inversion_check_delays=(0.0,),
    )
    oco.dispatcher.protective_leg_manager = mgr
    return mgr


@pytest.fixture
def alerts() -> Any:
    with patch.object(
        alert_publisher, "publish", new=AsyncMock(return_value=True)
    ) as m:
        yield m


def _leg_order(kind: str, symbol: str = DOT, **kw: Any) -> TradeOrder:
    base: dict[str, Any] = {
        "symbol": symbol,
        "side": "sell",
        "amount": kw.pop("amount", 1.0),
        "position_side": kw.pop("position_side", "LONG"),
        "reduce_only": True,
        "status": OrderStatus.PENDING,
    }
    if kind == "stop":
        base.update(type="stop", stop_loss=kw.pop("stop_loss", 3.6))
    elif kind == "take_profit":
        base.update(type="take_profit", take_profit=kw.pop("take_profit", 4.4))
    elif kind == "stop_limit":
        base.update(type="stop_limit", stop_loss=3.6, target_price=3.55)
    else:
        base.update(type="take_profit_limit", take_profit=4.4, target_price=4.45)
    base.update(kw)
    return TradeOrder(**base)


def _counter(metric: Any, **labels: str) -> float:
    return metric.labels(**labels)._value.get()


async def _arm_side(oco: OCOManager, qty: float, side: str = "LONG") -> dict[str, Any]:
    result = await oco.place_oco_orders(
        position_id="pos-1",
        symbol=DOT,
        position_side=side,
        quantity=qty,
        stop_loss_price=3.6 if side == "LONG" else 4.4,
        take_profit_price=4.4 if side == "LONG" else 3.6,
        strategy_position_id=None,
        entry_price=4.0,
    )
    assert result["status"] == "success", result
    return result


# ---------------------------------------------------------------------------
# Mode flag
# ---------------------------------------------------------------------------
def test_default_mode_is_explicit_qty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TE_PROTECTIVE_LEG_MODE", raising=False)
    assert protective_leg_mode() == "explicit_qty"


def test_invalid_mode_falls_back_to_explicit_qty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "closeposition")
    assert protective_leg_mode() == "explicit_qty"
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", " Close_Position ")
    assert protective_leg_mode() == "close_position"


# ---------------------------------------------------------------------------
# AC1: default flag -> SL/TP requests carry quantity == side qty, no closePosition
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "binance_type"),
    [
        ("stop", "STOP_MARKET"),
        ("take_profit", "TAKE_PROFIT_MARKET"),
        ("stop_limit", "STOP"),
        ("take_profit_limit", "TAKE_PROFIT"),
    ],
)
async def test_ac1_explicit_leg_carries_side_quantity(
    exchange: Any, client: FakeFuturesClient, kind: str, binance_type: str
) -> None:
    # Two strategies share the side: 82.8 DOT in total. The caller only knows
    # its own 30 DOT entry — the leg must cover the whole side.
    client.set_position(DOT, "LONG", 82.8)

    result = await exchange.execute(_leg_order(kind, amount=30.0))

    assert result["status"] == "NEW", result
    assert result["is_algo_order"] is True
    [sent] = client.posts()
    assert sent["quantity"] == "82.8"
    assert "closePosition" not in sent
    assert "reduceOnly" not in sent  # not allowed with positionSide (hedge mode)
    assert sent["positionSide"] == "LONG"
    assert sent["side"] == "SELL"
    assert sent["type"] == binance_type
    assert sent["timeInForce"] == "GTC"
    assert sent["workingType"] == "MARK_PRICE"
    assert sent["algoType"] == "CONDITIONAL"


@pytest.mark.asyncio
async def test_ac1_oco_pair_legs_are_side_sized(
    oco: OCOManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 82.8)
    await _arm_side(oco, qty=40.0)
    posts = client.posts()
    assert [p["type"] for p in posts] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert all(p["quantity"] == "82.8" for p in posts)
    assert all("closePosition" not in p for p in posts)


@pytest.mark.asyncio
async def test_quantity_falls_back_to_request_when_side_not_yet_visible(
    exchange: Any, client: FakeFuturesClient
) -> None:
    """positionRisk can lag a fresh entry (#445): a flat reading must not size
    the leg to zero."""
    await exchange.execute(_leg_order("stop", amount=12.3))
    assert client.posts()[0]["quantity"] == "12.3"


@pytest.mark.asyncio
async def test_quantity_falls_back_to_request_when_side_inverted(
    exchange: Any, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", -8.9)
    await exchange.execute(_leg_order("stop", amount=5.0))
    assert client.posts()[0]["quantity"] == "5.0"


@pytest.mark.asyncio
async def test_quantity_is_floored_never_rounded_up(
    exchange: Any, client: FakeFuturesClient
) -> None:
    client.set_position("BTCUSDT", "LONG", 0.0129)  # not a step multiple
    await exchange.execute(
        _leg_order("stop", symbol="BTCUSDT", amount=0.0129, stop_loss=45000.0)
    )
    assert client.posts()[0]["quantity"] == "0.012"


@pytest.mark.asyncio
async def test_quantity_below_min_qty_is_refused(
    exchange: Any, client: FakeFuturesClient
) -> None:
    result = await exchange.execute(
        _leg_order("stop", symbol="BTCUSDT", amount=0.0004, stop_loss=45000.0)
    )
    assert result["status"] == "failed"
    assert "protective_leg_quantity_below_min" in result["error"]
    assert client.posts() == []


@pytest.mark.asyncio
async def test_one_way_mode_leg_sends_reduce_only(
    exchange: Any, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "BOTH", 7.0)
    await exchange.execute(_leg_order("stop", amount=7.0, position_side=None))
    [sent] = client.posts()
    assert sent["reduceOnly"] is True
    assert "positionSide" not in sent
    assert sent["quantity"] == "7.0"


@pytest.mark.asyncio
async def test_positionrisk_failure_uses_requested_quantity(
    exchange: Any, client: FakeFuturesClient
) -> None:
    client.futures_position_information = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("positionRisk down")
    )
    await exchange.execute(_leg_order("take_profit", amount=3.3))
    assert client.posts()[0]["quantity"] == "3.3"


@pytest.mark.asyncio
async def test_ambiguous_placement_failure_does_not_stack_duplicate(
    exchange: Any, client: FakeFuturesClient
) -> None:
    """A timeout after Binance accepted the leg must not be retried into a
    second full-size leg (explicit legs are not deduplicated by -4130)."""
    client.set_position(DOT, "LONG", 10.0)
    client.post_lands_then_raises.append(ConnectionError("read timeout"))

    result = await exchange.execute(_leg_order("stop", amount=10.0))

    assert result["status"] == "NEW"
    assert len(client.posts()) == 1
    assert len(client.open_legs(DOT, "LONG")) == 1


@pytest.mark.asyncio
async def test_failed_placement_that_did_not_land_is_retried(
    exchange: Any, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    client.post_errors.append(ConnectionError("connection reset"))
    with patch("tradeengine.exchange.binance.asyncio.sleep", new=AsyncMock()):
        result = await exchange.execute(_leg_order("stop", amount=10.0))
    assert result["status"] == "NEW"
    assert len(client.posts()) == 2
    assert len(client.open_legs(DOT, "LONG")) == 1


# ---------------------------------------------------------------------------
# AC6: TE_PROTECTIVE_LEG_MODE=close_position -> requests match today's
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "stop",
            [
                ("symbol", DOT),
                ("side", "SELL"),
                ("type", "STOP_MARKET"),
                ("algoType", "CONDITIONAL"),
                ("timeInForce", "GTE_GTC"),
                ("closePosition", True),
                ("triggerPrice", "3.600"),
                ("workingType", "MARK_PRICE"),
                ("priceProtect", True),
                ("positionSide", "LONG"),
            ],
        ),
        (
            "stop_limit",
            [
                ("symbol", DOT),
                ("side", "SELL"),
                ("type", "STOP"),
                ("algoType", "CONDITIONAL"),
                ("timeInForce", "GTE_GTC"),
                ("closePosition", True),
                ("price", "3.550"),
                ("triggerPrice", "3.600"),
                ("workingType", "MARK_PRICE"),
                ("priceProtect", True),
                ("positionSide", "LONG"),
            ],
        ),
        (
            "take_profit",
            [
                ("symbol", DOT),
                ("side", "SELL"),
                ("type", "TAKE_PROFIT_MARKET"),
                ("algoType", "CONDITIONAL"),
                ("timeInForce", "GTE_GTC"),
                ("closePosition", True),
                ("triggerPrice", "4.400"),
                ("workingType", "MARK_PRICE"),
                ("priceProtect", True),
                ("positionSide", "LONG"),
            ],
        ),
        (
            "take_profit_limit",
            [
                ("symbol", DOT),
                ("side", "SELL"),
                ("type", "TAKE_PROFIT"),
                ("algoType", "CONDITIONAL"),
                ("timeInForce", "GTE_GTC"),
                ("closePosition", True),
                ("price", "4.450"),
                ("triggerPrice", "4.400"),
                ("workingType", "MARK_PRICE"),
                ("priceProtect", True),
                ("positionSide", "LONG"),
            ],
        ),
    ],
)
async def test_ac6_close_position_mode_is_byte_for_byte_legacy(
    monkeypatch: pytest.MonkeyPatch,
    exchange: Any,
    client: FakeFuturesClient,
    kind: str,
    expected: list[tuple[str, Any]],
) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "close_position")
    client.set_position(DOT, "LONG", 82.8)

    await exchange.execute(_leg_order(kind, amount=30.0))

    [sent] = client.posts()
    assert list(sent.items()) == expected  # same keys, values AND order
    assert client.position_reads == 0  # no positionRisk lookup either


# ---------------------------------------------------------------------------
# AC2: side 10 -> 6 re-places both legs at 6 (old legs cancelled via algo API)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac2_resize_side_10_to_6(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    placed = await _arm_side(oco, qty=10.0)
    old_sl, old_tp = placed["sl_order_id"], placed["tp_order_id"]
    assert {o["quantity"] for o in client.open_legs(DOT, "LONG")} == {"10.0"}
    oco.exchange.cancel_algo_order = AsyncMock(  # type: ignore[method-assign]
        wraps=oco.exchange.cancel_algo_order
    )

    client.set_position(DOT, "LONG", 6.0)  # a partial close
    outcome = await manager.sync_side(DOT, "LONG", reason="test")

    assert outcome["status"] == "synced"
    cancelled = {c.args[1] for c in oco.exchange.cancel_algo_order.await_args_list}
    assert cancelled == {old_sl, old_tp}
    legs = client.open_legs(DOT, "LONG")
    assert sorted((o["orderType"], o["quantity"], o["triggerPrice"]) for o in legs) == [
        ("STOP_MARKET", "6.0", "3.600"),
        ("TAKE_PROFIT_MARKET", "6.0", "4.400"),
    ]
    assert client.standard_cancels == []
    # The tracked OCO pair follows the new algo ids (else the monitor would
    # read the resize as a fill).
    pair = oco.active_oco_pairs[f"{DOT}_LONG"][0]
    new_ids = {str(o["algoId"]) for o in legs}
    assert {pair["sl_order_id"], pair["tp_order_id"]} == new_ids
    assert pair["status"] == "active"


@pytest.mark.asyncio
async def test_resize_grows_legs_when_side_grows(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 6.0)
    await _arm_side(oco, qty=6.0)
    client.set_position(DOT, "LONG", 10.0)  # another strategy consolidated in

    await manager.sync_side(DOT, "LONG")

    assert {o["quantity"] for o in client.open_legs(DOT, "LONG")} == {"10.0"}
    assert len(client.open_legs(DOT, "LONG")) == 2


@pytest.mark.asyncio
async def test_resize_cancels_before_placing(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    """Same-kind legs never add up to more than the side at any instant."""
    client.set_position(DOT, "LONG", 10.0)
    await _arm_side(oco, qty=10.0)
    client.set_position(DOT, "LONG", 6.0)
    client.requests.clear()

    await manager.sync_side(DOT, "LONG")

    ops = [(m, d.get("type")) for m, p, d in client.requests if p == "algoOrder"]
    assert ops == [
        ("delete", None),
        ("post", "STOP_MARKET"),
        ("delete", None),
        ("post", "TAKE_PROFIT_MARKET"),
    ]


@pytest.mark.asyncio
async def test_resize_preserves_exact_trigger_without_safety_floor(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    """The market drifted to within the 6% floor of the live SL: re-running
    the placement floor would refuse the stop and leave the side naked."""
    client.set_position(DOT, "LONG", 10.0)
    sl_id = client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        quantity="10.0",
        triggerPrice="3.900",  # 2.5% below market 4.0
        createTime=OLD_MS,
    )
    client.set_position(DOT, "LONG", 4.0)

    await manager.sync_side(DOT, "LONG")

    [leg] = client.open_legs(DOT, "LONG")
    assert (leg["quantity"], leg["triggerPrice"]) == ("4.0", "3.900")
    assert str(leg["algoId"]) != sl_id


@pytest.mark.asyncio
async def test_resize_failure_alerts_and_counts(
    oco: OCOManager,
    manager: ProtectiveLegManager,
    client: FakeFuturesClient,
    alerts: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        quantity="10.0",
        triggerPrice="3.600",
        createTime=OLD_MS,
    )
    client.set_position(DOT, "LONG", 6.0)
    # a definitive (non-retryable) rejection of the replacement leg
    client.post_errors.append(api_error(-4131, "PERCENT_PRICE filter limit."))
    before = _counter(
        protective_leg_sync_actions_total, action="resize", outcome="failed"
    )

    with caplog.at_level(logging.CRITICAL):
        await manager.sync_side(DOT, "LONG")

    assert (
        _counter(protective_leg_sync_actions_total, action="resize", outcome="failed")
        == before + 1
    )
    assert "RESIZE FAILED" in caplog.text
    assert (
        alerts.await_args.kwargs["alert_name"] == f"protective_leg_resize_failed.{DOT}"
    )


@pytest.mark.asyncio
async def test_resize_aborts_when_leg_vanished(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    sl_id = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="10.0", triggerPrice="3.6"
    )
    client.set_position(DOT, "LONG", 6.0)
    client.cancel_errors[sl_id] = api_error(-2011, "Unknown order sent.")

    outcome = await manager.sync_side(DOT, "LONG")

    assert client.posts() == []  # never re-place a leg that may have fired
    assert any(a.startswith("resize_aborted_leg_gone") for a in outcome["actions"])


# ---------------------------------------------------------------------------
# AC3: side reaches 0 -> every open leg for that side is cancelled
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac3_flat_side_cancels_every_leg(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    for order_type, trig in (("STOP_MARKET", "3.6"), ("TAKE_PROFIT_MARKET", "4.4")):
        client.add_algo_order(
            symbol=DOT,
            orderType=order_type,
            quantity="10.0",
            triggerPrice=trig,
            createTime=OLD_MS,
        )
    # a legacy closePosition leg on the same side goes too
    client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        closePosition=True,
        triggerPrice="3.5",
        createTime=OLD_MS,
    )
    # the other side of the symbol is untouched
    client.set_position(DOT, "SHORT", -5.0)
    other = client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        side="BUY",
        positionSide="SHORT",
        quantity="5.0",
        triggerPrice="4.4",
        createTime=OLD_MS,
    )
    client.set_position(DOT, "LONG", 0.0)

    outcome = await manager.sync_side(DOT, "LONG")

    assert outcome["status"] == "flat"
    assert client.open_legs(DOT, "LONG") == []
    assert other in client.algo_orders


@pytest.mark.asyncio
async def test_flat_side_spares_young_legs(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    """positionRisk can lag a fresh entry: a just-placed leg is not an orphan."""
    client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="1.0", triggerPrice="3.6"
    )
    outcome = await manager.sync_side(DOT, "LONG")
    assert outcome["deferred_young_legs"] is True
    assert len(client.open_legs(DOT, "LONG")) == 1


@pytest.mark.asyncio
async def test_flat_side_marks_tracked_pair_cancelled(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    await _arm_side(oco, qty=10.0)
    for leg in client.open_legs(DOT, "LONG"):
        leg["createTime"] = OLD_MS
    client.set_position(DOT, "LONG", 0.0)  # closed by a MARKET order elsewhere

    await manager.sync_side(DOT, "LONG")

    assert client.open_legs(DOT, "LONG") == []
    pair = oco.active_oco_pairs[f"{DOT}_LONG"][0]
    assert pair["status"] == "cancelled"


@pytest.mark.asyncio
async def test_close_position_mode_leaves_closeposition_legs_to_binance(
    monkeypatch: pytest.MonkeyPatch,
    manager: ProtectiveLegManager,
    client: FakeFuturesClient,
) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "close_position")
    legacy = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, createTime=OLD_MS
    )
    explicit = client.add_algo_order(
        symbol=DOT,
        orderType="TAKE_PROFIT_MARKET",
        quantity="3.0",
        triggerPrice="4.4",
        createTime=OLD_MS,
    )

    await manager.sync_side(DOT, "LONG")

    assert legacy in client.algo_orders  # GTE_GTC sweeps it, as before #651
    assert explicit not in client.algo_orders  # left over from explicit mode


@pytest.mark.asyncio
async def test_close_position_mode_still_cancels_legs_on_inverted_side(
    monkeypatch: pytest.MonkeyPatch,
    manager: ProtectiveLegManager,
    client: FakeFuturesClient,
    alerts: AsyncMock,
) -> None:
    """GTE_GTC does not sweep a leg on an inverted (non-zero) side."""
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "close_position")
    client.set_position(DOT, "LONG", -8.9)
    orphan = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, triggerPrice="1.1"
    )
    await manager.sync_side(DOT, "LONG")
    assert orphan not in client.algo_orders
    assert client.posts() == []


# ---------------------------------------------------------------------------
# AC4: the startup sweep cancels an explicit-quantity leg whose side is 0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac4_startup_sweep_cancels_leg_on_flat_side(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    orphan = client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        quantity="82.8",
        triggerPrice="3.6",
        createTime=OLD_MS,
    )
    healthy_sl = client.add_algo_order(
        symbol="BTCUSDT",
        orderType="STOP_MARKET",
        quantity="0.010",
        triggerPrice="45000.0",
        createTime=OLD_MS,
    )
    client.set_position("BTCUSDT", "LONG", 0.01)

    await manager.start()
    try:
        assert orphan not in client.algo_orders
        assert healthy_sl in client.algo_orders
        assert client.standard_cancels == []
    finally:
        await manager.stop()
    assert manager.running is False


# ---------------------------------------------------------------------------
# AC5: a fill that leaves the side inverted -> CRITICAL alert, NO order placed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac5_inverting_fill_alerts_and_places_nothing(
    oco: OCOManager,
    manager: ProtectiveLegManager,
    client: FakeFuturesClient,
    alerts: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.set_position(DOT, "LONG", 82.8)
    placed = await _arm_side(oco, qty=82.8)
    posts_before = len(client.posts())
    inversions_before = _counter(
        protective_fill_inversion_total,
        symbol=DOT,
        side="LONG",
        source="protective_fill",
    )

    # The testnet executes the TP as position + residual: LONG ends at -8.9.
    client.fill_leg(placed["tp_order_id"], -8.9)
    with caplog.at_level(logging.CRITICAL):
        await oco._monitor_iteration()  # OCO monitor sees the fill
        await asyncio.gather(*list(manager._background))  # post-fill guard

    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical and "SIGN-INVERTED" in critical[0].getMessage()
    assert (
        _counter(
            protective_fill_inversion_total,
            symbol=DOT,
            side="LONG",
            source="protective_fill",
        )
        == inversions_before + 1
    )
    alert = alerts.await_args.kwargs
    assert alert["alert_name"] == f"protective_fill_inversion.{DOT}"
    assert alert["severity"] == "critical"
    assert alert["payload"]["position_amt"] == -8.9
    # NO order of any kind was placed to "correct" it.
    assert len(client.posts()) == posts_before
    assert client.created_orders == []


@pytest.mark.asyncio
async def test_inversion_guard_is_read_only(
    manager: ProtectiveLegManager, client: FakeFuturesClient, alerts: AsyncMock
) -> None:
    client.set_position(DOT, "SHORT", 9470.2)
    manager._exchange.cancel_algo_order = AsyncMock()  # type: ignore[method-assign]

    assert await manager.check_inversion_after_fill(DOT, "SHORT") is True

    manager._exchange.cancel_algo_order.assert_not_awaited()
    assert client.posts() == [] and client.created_orders == []
    alerts.assert_awaited_once()


@pytest.mark.asyncio
async def test_inversion_guard_quiet_when_flat_or_healthy(
    manager: ProtectiveLegManager, client: FakeFuturesClient, alerts: AsyncMock
) -> None:
    client.set_position(DOT, "LONG", 0.0)
    assert await manager.check_inversion_after_fill(DOT, "LONG") is False
    client.set_position(DOT, "LONG", 3.0)
    assert await manager.check_inversion_after_fill(DOT, "LONG") is False
    alerts.assert_not_awaited()


@pytest.mark.asyncio
async def test_inverted_side_sync_never_places_and_cancels_hazard_legs(
    manager: ProtectiveLegManager, client: FakeFuturesClient, alerts: AsyncMock
) -> None:
    """DOT LONG -8.9 with the orphaned SELL SL (#650): the SL can only deepen
    the inversion. It is cancelled; nothing is placed; one alert per episode."""
    client.set_position(DOT, "LONG", -8.9)
    orphan = client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        closePosition=True,
        triggerPrice="1.1565",
    )

    first = await manager.sync_side(DOT, "LONG")
    second = await manager.sync_side(DOT, "LONG")

    assert first["status"] == second["status"] == "inverted"
    assert orphan not in client.algo_orders
    assert client.posts() == [] and client.created_orders == []
    assert alerts.await_count == 1  # latched per episode

    client.set_position(DOT, "LONG", 0.0)  # operator flattened
    await manager.sync_side(DOT, "LONG")
    client.set_position(DOT, "LONG", -1.0)  # a new episode alerts again
    await manager.sync_side(DOT, "LONG")
    assert alerts.await_count == 2


# ---------------------------------------------------------------------------
# Duplicates and legacy closePosition legs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_explicit_legs_are_deduplicated(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    placed = await _arm_side(oco, qty=10.0)
    dup = client.add_algo_order(  # e.g. the remediator re-armed the side
        symbol=DOT, orderType="STOP_MARKET", quantity="10.0", triggerPrice="3.5"
    )

    await manager.sync_side(DOT, "LONG")

    kinds = sorted(o["orderType"] for o in client.open_legs(DOT, "LONG"))
    assert kinds == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert dup not in client.algo_orders
    assert placed["sl_order_id"] in client.algo_orders  # tracked leg kept


@pytest.mark.asyncio
async def test_cancelled_tracked_duplicate_repoints_pair(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    keep = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="10.0", triggerPrice="3.6"
    )
    extra = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="10.0", triggerPrice="3.5"
    )
    tp = client.add_algo_order(
        symbol=DOT, orderType="TAKE_PROFIT_MARKET", quantity="10.0", triggerPrice="4.4"
    )
    first = {"sl_order_id": keep, "tp_order_id": tp, "status": "active"}
    second = {"sl_order_id": extra, "tp_order_id": tp, "status": "active"}
    oco.active_oco_pairs[f"{DOT}_LONG"] = [first, second]

    await manager.sync_side(DOT, "LONG")

    assert extra not in client.algo_orders
    assert second["sl_order_id"] == keep  # never looks like a fill to the monitor


@pytest.mark.asyncio
async def test_legacy_closeposition_leg_is_migrated_place_first(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 82.8)
    legacy_sl = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, triggerPrice="3.600"
    )
    legacy_tp = client.add_algo_order(
        symbol=DOT,
        orderType="TAKE_PROFIT_MARKET",
        closePosition=True,
        triggerPrice="4.400",
    )
    oco.active_oco_pairs[f"{DOT}_LONG"] = [
        {"sl_order_id": legacy_sl, "tp_order_id": legacy_tp, "status": "active"}
    ]
    client.requests.clear()

    await manager.sync_side(DOT, "LONG")

    legs = client.open_legs(DOT, "LONG")
    assert sorted(
        (o["orderType"], o["quantity"], o["closePosition"]) for o in legs
    ) == [
        ("STOP_MARKET", "82.8", False),
        ("TAKE_PROFIT_MARKET", "82.8", False),
    ]
    ops = [m for m, p, _ in client.requests if p == "algoOrder"]
    assert ops == ["post", "delete", "post", "delete"]  # replacement first
    pair = oco.active_oco_pairs[f"{DOT}_LONG"][0]
    assert {pair["sl_order_id"], pair["tp_order_id"]} == {
        str(o["algoId"]) for o in legs
    }


@pytest.mark.asyncio
async def test_failed_migration_keeps_legacy_leg_and_backs_off(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 82.8)
    legacy = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, triggerPrice="3.6"
    )
    client.post_errors.append(api_error(-4131, "PERCENT_PRICE filter limit."))

    first = await manager.sync_side(DOT, "LONG")
    second = await manager.sync_side(DOT, "LONG")

    assert legacy in client.algo_orders  # the side stays protected
    assert any(a.startswith("migrate_failed") for a in first["actions"])
    assert any(a.startswith("migrate_backoff") for a in second["actions"])


@pytest.mark.asyncio
async def test_migration_can_be_disabled(
    exchange: Any, oco: OCOManager, client: FakeFuturesClient
) -> None:
    mgr = ProtectiveLegManager(exchange, oco, migrate_legacy=False)
    client.set_position(DOT, "LONG", 82.8)
    legacy = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, triggerPrice="3.6"
    )
    await mgr.sync_side(DOT, "LONG")
    assert legacy in client.algo_orders
    assert client.posts() == []


@pytest.mark.asyncio
async def test_explicit_leg_supersedes_legacy_leg_of_same_kind(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 5.0)
    legacy = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", closePosition=True, triggerPrice="3.5"
    )
    explicit = client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="5.0", triggerPrice="3.6"
    )
    await manager.sync_side(DOT, "LONG")
    assert legacy not in client.algo_orders
    assert explicit in client.algo_orders


# ---------------------------------------------------------------------------
# Coordination with the OCO monitor
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_defers_while_tracked_pair_is_mid_fill(
    oco: OCOManager, manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    placed = await _arm_side(oco, qty=10.0)
    client.fill_leg(placed["sl_order_id"], 0.0)  # monitor has not run yet

    outcome = await manager.sync_side(DOT, "LONG")

    assert outcome == {"status": "deferred", "reason": "oco_fill_in_progress"}
    assert placed["tp_order_id"] in client.algo_orders  # the monitor owns it


@pytest.mark.asyncio
async def test_sync_defers_while_a_leg_is_triggering(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 6.0)
    client.add_algo_order(
        symbol=DOT,
        orderType="STOP_MARKET",
        quantity="10.0",
        triggerPrice="3.6",
        algoStatus="TRIGGERING",
    )
    outcome = await manager.sync_side(DOT, "LONG")
    assert outcome["status"] == "deferred"
    assert client.posts() == []


@pytest.mark.asyncio
async def test_monitor_waits_for_side_lock(oco: OCOManager) -> None:
    oco.active_oco_pairs[f"{DOT}_LONG"] = [
        {
            "symbol": DOT,
            "position_side": "LONG",
            "sl_order_id": "1",
            "tp_order_id": "2",
            "status": "active",
        }
    ]
    oco.exchange.get_all_open_orders = AsyncMock(return_value={"1", "2"})  # type: ignore[method-assign]
    lock = oco.side_lock(f"{DOT}_LONG")
    await lock.acquire()
    task = asyncio.create_task(oco._monitor_iteration())
    await asyncio.sleep(0.05)
    assert oco.exchange.get_all_open_orders.await_count == 0
    lock.release()
    await task
    assert oco.exchange.get_all_open_orders.await_count == 1


@pytest.mark.asyncio
async def test_truth_unavailable_takes_no_action(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.add_algo_order(symbol=DOT, createTime=OLD_MS, quantity="1.0")
    client.futures_position_information = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("down")
    )
    outcome = await manager.sync_side(DOT, "LONG")
    assert outcome == {"status": "skipped", "reason": "truth_unavailable"}
    assert client.deletes() == []


@pytest.mark.asyncio
async def test_non_hedge_side_is_not_managed(manager: ProtectiveLegManager) -> None:
    assert (await manager.sync_side(DOT, "BOTH"))["status"] == "skipped"


# ---------------------------------------------------------------------------
# Scheduling: request_sync / periodic sweep
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_sync_runs_side_sync_via_loop(
    exchange: Any, oco: OCOManager
) -> None:
    mgr = ProtectiveLegManager(exchange, oco, sync_interval_sec=3600)
    mgr.sync_side = AsyncMock(return_value={})  # type: ignore[method-assign]
    await mgr.start(startup_sweep=False)
    try:
        mgr.request_sync("dotusdt", "long", reason="t", delays=(0.0,))
        mgr.request_sync(None, "LONG")  # ignored
        mgr.request_sync(DOT, "BOTH")  # ignored
        for _ in range(50):
            if mgr.sync_side.await_count:
                break
            await asyncio.sleep(0.02)
        mgr.sync_side.assert_awaited_with(DOT, "LONG", reason="scheduled")
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_request_sync_is_noop_when_not_running(
    manager: ProtectiveLegManager,
) -> None:
    manager.request_sync(DOT, "LONG")
    assert manager._pending == {}


@pytest.mark.asyncio
async def test_periodic_sweep_only_syncs_sides_needing_attention(
    manager: ProtectiveLegManager, client: FakeFuturesClient
) -> None:
    client.set_position(DOT, "LONG", 10.0)
    client.add_algo_order(
        symbol=DOT, orderType="STOP_MARKET", quantity="10.0", triggerPrice="3.6"
    )  # converged
    client.set_position("BTCUSDT", "LONG", 0.01)
    client.add_algo_order(
        symbol="BTCUSDT",
        orderType="STOP_MARKET",
        quantity="0.020",
        triggerPrice="45000.0",
        createTime=OLD_MS,
    )  # oversized

    counts = await manager.sync_all(reason="periodic")

    assert counts == {"checked": 2, "synced": 1}
    [btc_leg] = client.open_legs("BTCUSDT", "LONG")
    assert btc_leg["quantity"] == "0.010"


@pytest.mark.asyncio
async def test_on_protective_fill_schedules_guard(
    manager: ProtectiveLegManager, client: FakeFuturesClient, alerts: AsyncMock
) -> None:
    client.set_position(DOT, "LONG", -0.303)
    manager.on_protective_fill(DOT, "LONG", "123")
    manager.on_protective_fill(DOT, "BOTH", "124")  # ignored
    await asyncio.gather(*list(manager._background))
    alerts.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_pure_helpers() -> None:
    assert leg_kind_of({"orderType": "TAKE_PROFIT"}) == "TP"
    assert leg_kind_of({"type": "STOP"}) == "SL"
    assert leg_kind_of({"orderType": "LIMIT"}) is None
    assert is_inverted("LONG", -1) and is_inverted("SHORT", 1)
    assert not is_inverted("LONG", 1) and not is_inverted("BOTH", -1)
    assert is_gone_error(api_error(-2011, "Unknown order sent."))
    assert is_gone_error(Exception("APIError(code=-4029): x"))
    assert is_gone_error(Exception("Order does not exist."))
    assert not is_gone_error(api_error(-1001, "disconnected"))
    assert ProtectiveLeg.from_order({"orderType": "STOP_MARKET"}) is None  # no id
    orders = [
        {
            "algoId": 1,
            "symbol": DOT,
            "orderType": "STOP_MARKET",
            "side": "SELL",
            "positionSide": "LONG",
        },
        {
            "algoId": 2,
            "symbol": DOT,
            "orderType": "STOP_MARKET",
            "side": "BUY",
            "positionSide": "LONG",
        },
        {
            "algoId": 3,
            "symbol": "X",
            "orderType": "STOP_MARKET",
            "side": "SELL",
            "positionSide": "LONG",
        },
        "garbage",
    ]
    assert [leg.algo_id for leg in side_legs(orders, DOT, "LONG")] == ["1"]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_place_protective_leg_rejects_below_min(exchange: Any) -> None:
    with pytest.raises(ValueError, match="below_min"):
        await exchange.place_protective_leg(
            symbol="BTCUSDT",
            position_side="LONG",
            side="SELL",
            order_type="STOP_MARKET",
            quantity=0.0001,
            trigger_price=45000.0,
        )
