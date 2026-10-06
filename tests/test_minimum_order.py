"""One shared minimum valid order, with a volatility-derived margin over MIN_NOTIONAL (#732).

Observed before: `_calculate_order_amount` enforced 0.0007 BTC (a fixed 5% buffer over the exact minimum)
while probe sizing cut the order to 0.0006, below it; alt-coin probes sat 0.6-2.6% over a $5 minimum.
"""

import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from petrosa_contracts import Signal

from contracts.order import TradeOrder
from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange.binance import BinanceFuturesExchange
from tradeengine.minimum_order import (
    FALLBACK_MARGIN,
    MIN_RETURNS,
    minimum_quantity,
    volatility_margin,
)

# symbol -> (step, minQty, min notional, price)
MARKETS = {
    "BTCUSDT": ("0.0001", "0.0001", "50", 85000.0),
    "ETHUSDT": ("0.001", "0.001", "20", 3000.0),
    "ALTUSDT": ("1", "1", "5", 0.5),  # a $5-minimum alt
}


@pytest.mark.parametrize("margin", [0.0, FALLBACK_MARGIN, 0.0035, 0.05])
@pytest.mark.parametrize("symbol", sorted(MARKETS))
def test_minimum_quantity_meets_minqty_and_notional_after_the_margin(symbol, margin):
    step, min_qty, min_notional, price = MARKETS[symbol]

    quantity = minimum_quantity(
        price=price,
        step=step,
        min_qty=min_qty,
        min_notional=min_notional,
        margin=margin,
    )

    assert quantity >= float(min_qty)
    # Still at least MIN_NOTIONAL if the price falls by the margin before placement.
    assert Decimal(str(quantity)) * Decimal(str(price)) * (
        Decimal(1) - Decimal(str(margin))
    ) >= Decimal(min_notional)
    # A whole number of steps.
    assert (Decimal(str(quantity)) / Decimal(step)) % 1 == 0
    # And not wastefully large: less than one step above the exact requirement.
    exact = max(
        Decimal(min_qty),
        Decimal(min_notional) / (Decimal(str(price)) * (1 - Decimal(str(margin)))),
    )
    assert Decimal(str(quantity)) - exact < Decimal(step)


def test_minimum_quantity_rejects_nonsense():
    with pytest.raises(ValueError, match="price") as bad_price:
        minimum_quantity(price=0, step="1", min_qty="1", min_notional="5", margin=0.02)
    with pytest.raises(ValueError, match="margin") as bad_margin:
        minimum_quantity(price=1, step="1", min_qty="1", min_notional="5", margin=1.0)
    assert "price" in str(bad_price.value)
    assert "margin" in str(bad_margin.value)


def test_volatility_margin_scales_with_the_observed_moves():
    calm = [100.0 * (1 + 0.0001 * ((-1) ** i)) for i in range(40)]
    wild = [100.0 * (1 + 0.003 * ((-1) ** i)) for i in range(40)]

    calm_margin = volatility_margin(calm)
    wild_margin = volatility_margin(wild)

    assert calm_margin is not None and wild_margin is not None
    assert 0 < calm_margin < wild_margin
    # Z x sigma x sqrt(latency / 60): sigma of the alternating 0.6% returns is about 0.6%.
    assert wild_margin == pytest.approx(3.0 * 0.006 * math.sqrt(15.0 / 60.0), rel=0.1)
    assert volatility_margin([100.0] * 40) == 0.0


def test_volatility_margin_needs_enough_prices():
    assert volatility_margin([100.0] * MIN_RETURNS) is None  # one return short
    assert volatility_margin([]) is None


def _exchange(closes=None, fail=False):
    exchange = MagicMock()

    def limits(symbol):
        step, min_qty, min_notional, _ = MARKETS[symbol]
        return {"step_size": step, "min_qty": min_qty, "min_notional": min_notional}

    async def price(symbol):
        return MARKETS[symbol][3]

    exchange.get_min_order_amount.side_effect = limits
    exchange.get_price = AsyncMock(side_effect=price)
    if fail:
        exchange.get_recent_closes = AsyncMock(side_effect=RuntimeError("no klines"))
    else:
        exchange.get_recent_closes = AsyncMock(return_value=closes or [])
    return exchange


def _real_exchange(closes=None):
    """A real exchange object (so calculate_min_order_amount is the production one) with stub I/O."""
    exchange = BinanceFuturesExchange()
    exchange.initialized = True
    exchange.symbol_info = {
        symbol: {
            "baseAsset": symbol[:-4],
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": notional},
                {"filterType": "LOT_SIZE", "minQty": min_qty, "stepSize": step},
            ],
        }
        for symbol, (step, min_qty, notional, _) in MARKETS.items()
    }

    async def price(symbol):
        return MARKETS[symbol][3]

    exchange.get_price = AsyncMock(side_effect=price)
    exchange.get_recent_closes = AsyncMock(return_value=closes or [])
    return exchange


def _config_manager(parameters):
    document = {
        "parameters": parameters,
        "version": 1,
        "created_at": "2026-10-07T10:00:00+00:00",
        "updated_at": "2026-10-07T10:00:00+00:00",
        "created_by": "operator",
        "metadata": {},
    }

    async def query(database, collection, filter=None, limit=None, **_):
        return {
            "data": [dict(document)] if collection == "trading_configs_global" else []
        }

    with patch("tradeengine.db.mongodb_client.DataManagerClient") as client:
        client.return_value._client.query = AsyncMock(side_effect=query)
        return TradingConfigManager(mongodb_client=DataManagerConfigClient())


def _dispatcher(exchange=None, parameters=None):
    dispatcher = Dispatcher(exchange=exchange or _exchange())
    dispatcher.position_manager = MagicMock()
    dispatcher.config_manager = _config_manager(parameters or {"probe_mode": True})
    return dispatcher


WILD = [85000.0 * (1 + 0.003 * ((-1) ** i)) for i in range(60)]


@pytest.mark.asyncio
async def test_the_margin_is_a_labelled_fallback_until_prices_are_known():
    dispatcher = _dispatcher(_exchange(fail=True))

    await dispatcher._refresh_notional_margin("BTCUSDT")

    assert dispatcher._notional_margin("BTCUSDT") == (FALLBACK_MARGIN, "fallback")


@pytest.mark.asyncio
async def test_the_margin_is_derived_from_recent_one_minute_closes():
    dispatcher = _dispatcher(_exchange(closes=WILD))

    await dispatcher._refresh_notional_margin("BTCUSDT")

    margin, source = dispatcher._notional_margin("BTCUSDT")
    assert source == "volatility"
    assert margin == pytest.approx(volatility_margin(WILD))
    assert margin != FALLBACK_MARGIN


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_last_good_margin():
    exchange = _exchange(closes=WILD)
    dispatcher = _dispatcher(exchange)
    await dispatcher._refresh_notional_margin("BTCUSDT")
    good = dispatcher._notional_margin("BTCUSDT")
    dispatcher._margin_cache["BTCUSDT"] = (good[0], -1e9)  # stale: refresh again
    exchange.get_recent_closes = AsyncMock(side_effect=RuntimeError("down"))

    await dispatcher._refresh_notional_margin("BTCUSDT")

    assert dispatcher._notional_margin("BTCUSDT") == good


def _signal(symbol, quantity):
    from datetime import UTC, datetime

    return Signal(
        strategy_id="s",
        symbol=symbol,
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=MARKETS[symbol][3],
        quantity=quantity,
        current_price=MARKETS[symbol][3],
        timestamp=datetime.now(UTC),
        source="petrosa-cio",
        strategy="s",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", sorted(MARKETS))
async def test_order_sizing_and_probe_sizing_use_the_same_minimum(symbol):
    exchange = _real_exchange(closes=WILD if symbol == "BTCUSDT" else None)
    dispatcher = _dispatcher(exchange)
    await dispatcher._refresh_notional_margin(symbol)
    price = MARKETS[symbol][3]

    with patch("tradeengine.api.binance_exchange", exchange):
        sized = dispatcher._calculate_order_amount(_signal(symbol, 1e-9))
    probe = dispatcher._probe_quantity(symbol, price)

    # The order that a tiny signal is raised to is exactly the probe quantity: neither is below the other.
    assert sized == pytest.approx(probe)
    assert probe >= float(MARKETS[symbol][1])


@pytest.mark.asyncio
async def test_probe_sizing_cannot_leave_an_order_below_the_minimum_order_sizing_enforces():
    exchange = _real_exchange(closes=WILD)
    dispatcher = _dispatcher(exchange)
    await dispatcher._refresh_notional_margin("BTCUSDT")
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=1.0,
        target_price=85000.0,
        position_side="LONG",
        simulate=False,
    )

    assert await dispatcher._apply_probe_sizing(order) is None

    with patch("tradeengine.api.binance_exchange", exchange):
        floor = dispatcher._calculate_order_amount(_signal("BTCUSDT", 1e-9))
    assert order.amount >= floor
    assert order.amount == pytest.approx(floor)


@pytest.mark.asyncio
async def test_a_ceiling_that_fits_only_the_bare_minimum_is_rejected():
    exchange = _exchange()
    exchange.get_min_order_amount.side_effect = lambda s: {
        "step_size": "0.01",
        "min_qty": "0.01",
        "min_notional": "5",
    }
    exchange.get_price = AsyncMock(return_value=100.0)
    dispatcher = _dispatcher(exchange, parameters={"max_position_size_usd": 5.5})
    order = TradeOrder(
        symbol="ALTUSDT",
        side="buy",
        type="market",
        amount=1.0,
        target_price=100.0,
        position_side="LONG",
        simulate=False,
    )
    dispatcher._emit_execution_event_from_order = AsyncMock()

    # 5.5 / 100 = 0.05 is the bare minimum (5 / 100) but not 5 / (100 x 0.98) rounded up = 0.06.
    result = await dispatcher._apply_max_usd_cap(order)

    assert result is not None
    assert result["reason"] == "max_position_size_usd"
    assert order.amount == 1.0


@pytest.mark.asyncio
async def test_state_reports_the_minimum_the_margin_and_its_source():
    import tradeengine.api as api

    dispatcher = _dispatcher(_exchange(closes=WILD))
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(
            dispatcher,
            "get_cio_state",
            return_value={"risk_limits": {"max_position_size_usd": 1000.0}},
        ),
    ):
        state = await api.get_state(symbol="BTCUSDT", side=None)

    minimum = state["risk_limits"]["order_minimum"]
    assert minimum["margin_source"] == "volatility"
    assert minimum["margin"] == pytest.approx(volatility_margin(WILD))
    assert minimum["quantity"] == pytest.approx(
        minimum_quantity(
            price=85000.0,
            step="0.0001",
            min_qty="0.0001",
            min_notional="50",
            margin=minimum["margin"],
        )
    )
    assert minimum["notional"] == pytest.approx(minimum["quantity"] * 85000.0)
    # In probe mode the cap /state reports is that notional.
    assert state["risk_limits"]["max_position_size_usd"] == pytest.approx(
        minimum["notional"]
    )


def _binance(filters):
    exchange = BinanceFuturesExchange()
    exchange.initialized = True
    exchange.symbol_info = {
        "BTCUSDT": {"baseAsset": "BTC", "quoteAsset": "USDT", "filters": filters}
    }
    return exchange


def test_the_stricter_of_lot_size_and_market_lot_size_applies():
    exchange = _binance(
        [
            {"filterType": "MIN_NOTIONAL", "notional": "50"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.002", "stepSize": "0.002"},
        ]
    )

    info = exchange.get_min_order_amount("BTCUSDT")

    assert info["min_qty"] == 0.002
    assert info["step_size"] == 0.002


def test_the_exchange_helper_delegates_to_the_shared_minimum():
    exchange = _binance(
        [
            {"filterType": "MIN_NOTIONAL", "notional": "50"},
            {"filterType": "LOT_SIZE", "minQty": "0.0001", "stepSize": "0.0001"},
        ]
    )

    assert exchange.calculate_min_order_amount("BTCUSDT", 85000.0) == pytest.approx(
        minimum_quantity(
            price=85000.0,
            step="0.0001",
            min_qty="0.0001",
            min_notional="50",
            margin=FALLBACK_MARGIN,
        )
    )
    assert exchange.calculate_min_order_amount("BTCUSDT", 85000.0, margin=0.0) == (
        pytest.approx(0.0006)
    )


@pytest.mark.asyncio
async def test_recent_closes_come_from_one_minute_candles():
    exchange = _binance([])
    exchange.client = MagicMock()
    exchange.client.futures_klines.return_value = [
        [0, "1", "2", "0.5", "1.5", "10"],
        [1, "1.5", "2", "1", "1.25", "10"],
    ]

    closes = await exchange.get_recent_closes("BTCUSDT", 2)

    assert closes == [1.5, 1.25]
    exchange.client.futures_klines.assert_called_once_with(
        symbol="BTCUSDT", interval="1m", limit=2
    )
