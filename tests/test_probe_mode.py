"""probe_mode sizes every entry order to the symbol's smallest valid order (#726).

Smallest valid order = max(LOT_SIZE minQty, MIN_NOTIONAL / price rounded UP to the step), from the live
exchange filters at the current price. It is a resolved config parameter like the others.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient
from tradeengine.defaults import validate_parameters
from tradeengine.dispatcher import Dispatcher
from tradeengine.metrics import sizing_cap_total

# symbol -> (step, minQty, min notional, price)
MARKETS = {
    "BTCUSDT": ("0.001", "0.001", "100", 60000.0),
    "ETHUSDT": ("0.001", "0.001", "20", 3000.0),
    "DOGEUSDT": ("1", "10", "5", 2.0),  # minQty dominates: 5 / 2 = 2.5 -> 3, minQty 10
}


def _document(parameters, **scope):
    return {
        "parameters": parameters,
        "version": 1,
        "created_at": "2026-10-07T10:00:00+00:00",
        "updated_at": "2026-10-07T10:00:00+00:00",
        "created_by": "operator",
        "metadata": {},
        **scope,
    }


def _config_manager(global_parameters=None, symbols=None):
    """A real config manager over a fake data-manager (documents carry no `_id`)."""
    symbols = symbols or {}

    async def query(database, collection, filter=None, limit=None, **_):
        if collection == "trading_configs_global" and global_parameters is not None:
            return {"data": [_document(global_parameters)]}
        if collection == "trading_configs_symbols" and filter:
            parameters = symbols.get(filter.get("symbol"))
            if parameters is not None:
                return {"data": [_document(parameters, symbol=filter["symbol"])]}
        return {"data": []}

    with patch("tradeengine.db.mongodb_client.DataManagerClient") as client:
        client.return_value._client.query = AsyncMock(side_effect=query)
        return TradingConfigManager(mongodb_client=DataManagerConfigClient())


def _exchange():
    exchange = MagicMock()

    def limits(symbol):
        step, min_qty, min_notional, _ = MARKETS[symbol]
        return {"step_size": step, "min_qty": min_qty, "min_notional": min_notional}

    async def price(symbol):
        return MARKETS[symbol][3]

    exchange.get_min_order_amount.side_effect = limits
    exchange.get_price = AsyncMock(side_effect=price)
    return exchange


def _dispatcher(global_parameters=None, symbols=None, exchange=None):
    dispatcher = Dispatcher(exchange=exchange or _exchange())
    dispatcher.position_manager = MagicMock()
    dispatcher.config_manager = _config_manager(global_parameters, symbols)
    return dispatcher


def _order(symbol="BTCUSDT", amount=1.0, **fields):
    return TradeOrder(
        symbol=symbol,
        side="buy",
        type="market",
        amount=amount,
        target_price=MARKETS[symbol][3],
        position_side="LONG",
        simulate=False,
        **fields,
    )


def _probes():
    return sizing_cap_total.labels(cap="probe")._value.get()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbol", "quantity", "notional"),
    [
        ("BTCUSDT", 0.002, 120.0),  # 100 / 60000 = 0.00167 -> rounded UP to 0.002
        ("ETHUSDT", 0.007, 21.0),  # 20 / 3000 = 0.00667 -> 0.007
        ("DOGEUSDT", 10.0, 20.0),  # minQty (10) beats 5 / 2 = 2.5 -> 3
    ],
)
async def test_probe_size_is_the_smallest_valid_order_per_symbol(
    symbol, quantity, notional
):
    dispatcher = _dispatcher({"probe_mode": True})

    size = await dispatcher.probe_size(symbol)

    assert size is not None
    assert size[0] == pytest.approx(quantity)
    assert size[1] == pytest.approx(notional)
    # Never below the exchange minimums, never more than one step above the exact minimum.
    step, min_qty, min_notional, price = MARKETS[symbol]
    assert size[0] >= float(min_qty)
    assert Decimal(str(size[0])) * Decimal(str(price)) >= Decimal(min_notional)


@pytest.mark.asyncio
async def test_probe_mode_sizes_every_entry_order_to_the_probe_size():
    dispatcher = _dispatcher({"probe_mode": True})
    big = _order("BTCUSDT", amount=1.0)  # $60,000
    tiny = _order("ETHUSDT", amount=0.0001)  # below the exchange minimum
    before = _probes()

    assert await dispatcher._apply_probe_sizing(big) is None
    assert await dispatcher._apply_probe_sizing(tiny) is None

    assert big.amount == pytest.approx(0.002)
    assert tiny.amount == pytest.approx(0.007)
    assert _probes() == before + 2


@pytest.mark.asyncio
async def test_probe_mode_off_leaves_the_order_to_the_normal_sizing():
    dispatcher = _dispatcher({"probe_mode": False})
    order = _order("BTCUSDT", amount=0.01)
    before = _probes()

    assert await dispatcher._apply_probe_sizing(order) is None

    assert order.amount == 0.01
    assert _probes() == before


@pytest.mark.asyncio
async def test_a_reduce_only_order_is_not_resized_in_probe_mode():
    dispatcher = _dispatcher({"probe_mode": True})
    order = _order("BTCUSDT", amount=1.5, reduce_only=True)

    assert await dispatcher._apply_probe_sizing(order) is None

    assert order.amount == 1.5


@pytest.mark.asyncio
async def test_probe_mode_resolves_per_symbol_over_the_global_value():
    dispatcher = _dispatcher(
        {"probe_mode": False}, symbols={"BTCUSDT": {"probe_mode": True}}
    )

    assert await dispatcher.resolve_probe_mode("BTCUSDT") is True
    assert await dispatcher.resolve_probe_mode("ETHUSDT") is False


@pytest.mark.asyncio
async def test_an_order_that_cannot_be_sized_in_probe_mode_is_rejected():
    exchange = _exchange()
    exchange.get_price = AsyncMock(side_effect=RuntimeError("no price"))
    dispatcher = _dispatcher({"probe_mode": True}, exchange=exchange)
    order = _order("BTCUSDT", amount=1.0)
    order.target_price = None
    dispatcher._emit_execution_event_from_order = AsyncMock()

    result = await dispatcher._apply_probe_sizing(order)

    assert result["reason"] == "probe_sizing_unavailable"
    assert order.amount == 1.0


@pytest.mark.asyncio
async def test_the_execute_path_probe_sizes_before_the_limits_and_the_ceiling():
    dispatcher = _dispatcher({"probe_mode": True, "max_position_size_usd": 1000.0})
    order = _order("BTCUSDT", amount=1.0)
    seen = []

    async def check(candidate):
        seen.append(candidate.amount)
        return False

    dispatcher.position_manager.check_position_limits = check
    dispatcher.position_manager.rejection_reason = "portfolio_exposure"
    dispatcher._emit_execution_event_from_order = AsyncMock()

    await dispatcher._execute_order_with_consensus(order)

    assert seen == [pytest.approx(0.002)]


@pytest.mark.asyncio
async def test_a_stored_ceiling_still_applies_on_top_of_the_probe_size():
    # BTC probe notional is $120; a stored ceiling of $50 cannot fit it above the exchange minimum.
    dispatcher = _dispatcher({"probe_mode": True, "max_position_size_usd": 50.0})
    order = _order("BTCUSDT", amount=1.0)
    dispatcher._emit_execution_event_from_order = AsyncMock()

    assert await dispatcher._apply_probe_sizing(order) is None
    result = await dispatcher._apply_max_usd_cap(order)

    assert result["reason"] == "max_position_size_usd"


@pytest.mark.asyncio
async def test_state_reports_the_probe_notional_per_symbol():
    import tradeengine.api as api

    dispatcher = _dispatcher({"probe_mode": True, "max_position_size_usd": 1000.0})
    state = {"risk_limits": {"max_position_size_usd": 1000.0}}
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(
            dispatcher,
            "get_cio_state",
            side_effect=lambda _s: {"risk_limits": dict(state["risk_limits"])},
        ),
    ):
        btc = await api.get_state(symbol="BTCUSDT", side=None)
        eth = await api.get_state(symbol="ETHUSDT", side=None)
        doge = await api.get_state(symbol="DOGEUSDT", side=None)

    assert btc["risk_limits"]["probe_mode"] is True
    assert btc["risk_limits"]["max_position_size_usd"] == pytest.approx(120.0)
    assert eth["risk_limits"]["max_position_size_usd"] == pytest.approx(21.0)
    assert doge["risk_limits"]["max_position_size_usd"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_state_with_probe_mode_off_reports_the_resolved_cap():
    import tradeengine.api as api

    dispatcher = _dispatcher({"max_position_size_usd": 110.0})
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(
            dispatcher,
            "get_cio_state",
            return_value={"risk_limits": {"max_position_size_usd": 1000.0}},
        ),
    ):
        state = await api.get_state(symbol="BTCUSDT", side=None)

    assert state["risk_limits"]["probe_mode"] is False
    assert state["risk_limits"]["max_position_size_usd"] == 110


def test_probe_mode_is_a_valid_boolean_parameter():
    assert validate_parameters({"probe_mode": True}) == (True, [])
    ok, errors = validate_parameters({"probe_mode": "yes"})
    assert ok is False
    assert any("probe_mode" in error for error in errors)
