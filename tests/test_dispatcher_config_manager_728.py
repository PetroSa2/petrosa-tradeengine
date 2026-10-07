"""The dispatcher's leverage-bound check reads the live resolved config (#728)."""

import inspect
import logging
from unittest.mock import MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine import api, api_filter_routes
from tradeengine.defaults import get_default_parameters
from tradeengine.dispatcher import Dispatcher
from tradeengine.leverage_bound_guard import LeverageBoundGuard


class StoredConfig:
    """A stand-in for the live TradingConfigManager: stored overrides resolve symbol-side, then symbol, then
    global, over the defaults (the manager's own resolution order)."""

    def __init__(self, overrides=None, error=None):
        self.overrides = overrides or {}  # key: (symbol, side) -> {param: value}
        self.error = error
        self.calls = []

    async def get_config(self, symbol=None, side=None, strategy_id=None):
        self.calls.append((symbol, side, strategy_id))
        if self.error:
            raise self.error
        resolved = get_default_parameters()
        for scope in ((None, None), (symbol, None), (symbol, side)):
            resolved.update(self.overrides.get(scope, {}))
        return resolved


def make_dispatcher(manager=None):
    d = Dispatcher.__new__(Dispatcher)
    d.logger = logging.getLogger("test-728")
    d.leverage_bound_guard = LeverageBoundGuard()
    d.config_manager = manager
    return d


def order(side="buy", position_side=None, symbol="BTCUSDT"):
    return TradeOrder(
        symbol=symbol,
        type="market",
        side=side,
        amount=0.01,
        simulate=False,
        position_side=position_side,
        strategy_metadata={"strategy_id": "s1"},
    )


@pytest.fixture(autouse=True)
def no_open_positions():
    with patch("tradeengine.dispatcher.strategy_position_manager") as manager:
        manager.get_all_open_strategy_positions.return_value = []
        yield manager


@pytest.fixture(autouse=True)
def no_routes_manager():
    previous = api_filter_routes._config_manager
    api_filter_routes._config_manager = None
    yield
    api_filter_routes._config_manager = previous


@pytest.mark.asyncio
async def test_a_stored_max_leverage_bound_override_is_enforced():
    manager = StoredConfig({(None, None): {"max_leverage_bound": 5}})
    passed, reason = await make_dispatcher(manager).check_leverage_bound(order())
    assert passed is False
    assert "exceeds operator bound 5x" in reason
    assert manager.calls[0] == ("BTCUSDT", "LONG", "s1")


@pytest.mark.asyncio
async def test_without_a_stored_override_the_defaults_apply():
    manager = StoredConfig()
    passed, reason = await make_dispatcher(manager).check_leverage_bound(order())
    assert (passed, reason) == (True, "")


@pytest.mark.asyncio
async def test_a_symbol_and_side_override_resolve_for_the_side_the_order_holds():
    manager = StoredConfig({("BTCUSDT", "LONG"): {"max_leverage_bound": 5}})
    d = make_dispatcher(manager)
    assert (await d.check_leverage_bound(order("buy")))[0] is False
    # a SELL that opens a SHORT is the SHORT scope: default bound
    assert (await d.check_leverage_bound(order("sell", "SHORT")))[0] is True
    # a SELL that closes a LONG resolves the LONG's config
    assert (await d.check_leverage_bound(order("sell", "LONG")))[0] is False
    # another symbol is untouched
    assert (await d.check_leverage_bound(order("buy", symbol="ETHUSDT")))[0] is True


@pytest.mark.asyncio
async def test_a_stored_portfolio_cap_is_enforced_against_the_open_positions(
    no_open_positions,
):
    no_open_positions.get_all_open_strategy_positions.return_value = [
        {"strategy_id": "s2", "symbol": "ETHUSDT", "side": "LONG"}
    ]
    manager = StoredConfig({(None, None): {"portfolio_leverage_cap": 15}})
    passed, reason = await make_dispatcher(manager).check_leverage_bound(order())
    assert passed is False and "15" in reason


@pytest.mark.asyncio
async def test_the_dispatcher_falls_back_to_the_manager_the_config_routes_hold():
    api_filter_routes._config_manager = StoredConfig(
        {(None, None): {"max_leverage_bound": 5}}
    )
    passed, _ = await make_dispatcher(None).check_leverage_bound(order())
    assert passed is False


@pytest.mark.asyncio
async def test_with_no_manager_at_all_the_defaults_apply_and_it_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="test-728"):
        passed, _ = await make_dispatcher(None).check_leverage_bound(order())
    assert passed is True
    assert "uses the code defaults" in caplog.text


@pytest.mark.asyncio
async def test_a_config_store_error_fails_closed():
    manager = StoredConfig(error=RuntimeError("mongo down"))
    passed, reason = await make_dispatcher(manager).check_leverage_bound(order())
    assert passed is False
    assert reason.startswith("leverage_bound_guard_error")


@pytest.mark.asyncio
async def test_resolve_trading_parameters_reads_the_dispatchers_manager():
    manager = StoredConfig({(None, None): {"max_leverage_bound": 7}})
    resolved = await make_dispatcher(manager).resolve_trading_parameters(
        "BTCUSDT", "buy"
    )
    assert resolved["max_leverage_bound"] == 7


def test_the_dispatcher_starts_without_a_manager_and_the_lifespan_sets_it():
    assert Dispatcher(exchange=MagicMock()).config_manager is None
    source = inspect.getsource(api.lifespan)
    assert "dispatcher.config_manager = trading_config_manager" in source
