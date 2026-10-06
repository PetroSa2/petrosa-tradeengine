"""The stored `max_position_size_usd` is enforced on the order path and reported by /state (#726).

The operator's containment value (110) was stored and resolved but nothing used it: /state reported the
static 1000.0 and the dispatcher never clamped an order's notional.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient
from tradeengine.dispatcher import Dispatcher
from tradeengine.metrics import sizing_cap_total


def _cap_manager(max_usd=None):
    """A real config manager over a fake data-manager that stores a global config (no `_id`)."""
    documents = []
    if max_usd is not None:
        documents = [
            {
                "parameters": {"max_position_size_usd": max_usd},
                "version": 1,
                "created_at": "2026-10-06T18:53:00+00:00",
                "updated_at": "2026-10-06T18:53:00+00:00",
                "created_by": "operator",
                "metadata": {},
            }
        ]

    async def query(database, collection, filter=None, limit=None, **_):
        data = documents if collection == "trading_configs_global" else []
        return {"data": [dict(item) for item in data]}

    with patch("tradeengine.db.mongodb_client.DataManagerClient") as client:
        client.return_value._client.query = AsyncMock(side_effect=query)
        return TradingConfigManager(mongodb_client=DataManagerConfigClient())


def _exchange(step="0.001", min_qty="0.001", min_notional="5"):
    exchange = MagicMock()
    exchange.get_min_order_amount.return_value = {
        "step_size": step,
        "min_qty": min_qty,
        "min_notional": min_notional,
    }
    return exchange


@pytest.fixture
def make_dispatcher():
    def build(max_usd=110, exchange=None):
        dispatcher = Dispatcher(exchange=exchange or _exchange())
        dispatcher.position_manager = MagicMock()
        dispatcher.config_manager = _cap_manager(max_usd)
        return dispatcher

    return build


def _order(amount, price, **fields):
    return TradeOrder(
        symbol="SOLUSDT",
        side="buy",
        type="market",
        amount=amount,
        target_price=price,
        position_side="LONG",
        simulate=False,
        **fields,
    )


def _clamps():
    return sizing_cap_total.labels(cap="max_usd")._value.get()


@pytest.mark.asyncio
async def test_state_reports_the_resolved_stored_value(make_dispatcher):
    import tradeengine.api as api

    dispatcher = make_dispatcher(110)
    state = {"risk_limits": {"max_position_size_usd": 1000.0}}
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(dispatcher, "get_cio_state", return_value=state),
    ):
        long_state = await api.get_state(symbol="BTCUSDT", side=None)
        side_state = await api.get_state(symbol="BTCUSDT", side="LONG")

    assert long_state["risk_limits"]["max_position_size_usd"] == 110
    assert side_state["risk_limits"]["max_position_size_usd"] == 110


@pytest.mark.asyncio
async def test_state_keeps_the_settings_value_when_the_config_cannot_be_read(
    make_dispatcher,
):
    import tradeengine.api as api

    dispatcher = make_dispatcher(110)
    dispatcher.config_manager = MagicMock(
        get_config=AsyncMock(side_effect=RuntimeError)
    )
    state = {"risk_limits": {"max_position_size_usd": 1000.0}}
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(dispatcher, "get_cio_state", return_value=state),
    ):
        result = await api.get_state(symbol="BTCUSDT", side=None)

    assert result["risk_limits"]["max_position_size_usd"] == 1000.0


@pytest.mark.asyncio
async def test_a_thousand_dollar_order_is_clamped_to_the_cap_and_counted(
    make_dispatcher,
):
    dispatcher = make_dispatcher(110)
    order = _order(amount=10.0, price=100.0)  # $1,000
    before = _clamps()

    rejection = await dispatcher._apply_max_usd_cap(order)

    assert rejection is None
    assert order.amount == pytest.approx(1.1)
    assert Decimal(str(order.amount)) * Decimal("100") <= 110
    assert _clamps() == before + 1


@pytest.mark.asyncio
async def test_the_quantity_rounds_down_to_the_step_never_up(make_dispatcher):
    dispatcher = make_dispatcher(110, exchange=_exchange(step="0.1", min_qty="0.1"))
    order = _order(amount=10.0, price=30.0)  # 110 / 30 = 3.666... -> 3.6

    assert await dispatcher._apply_max_usd_cap(order) is None

    assert order.amount == pytest.approx(3.6)
    assert Decimal(str(order.amount)) * Decimal("30") <= 110


@pytest.mark.asyncio
async def test_an_order_under_the_cap_is_left_alone(make_dispatcher):
    dispatcher = make_dispatcher(110)
    order = _order(amount=1.0, price=100.0)
    before = _clamps()

    assert await dispatcher._apply_max_usd_cap(order) is None

    assert order.amount == 1.0
    assert _clamps() == before


@pytest.mark.asyncio
async def test_an_order_that_cannot_fit_above_the_exchange_minimum_is_rejected(
    make_dispatcher,
):
    # $110 buys 0.001 BTC at $60,000 = $60, below the $100 minimum notional: reject, never round up.
    dispatcher = make_dispatcher(110, exchange=_exchange(min_notional="100"))
    order = _order(amount=0.01, price=60000.0)
    order.symbol = "BTCUSDT"
    dispatcher._emit_execution_event_from_order = AsyncMock()

    result = await dispatcher._apply_max_usd_cap(order)

    assert result is not None
    assert result["status"] == "rejected"
    assert result["reason"] == "max_position_size_usd"
    assert order.rejection_reason == "max_position_size_usd"
    assert order.amount == 0.01  # untouched, not rounded up


@pytest.mark.asyncio
async def test_a_reduce_only_order_is_not_clamped(make_dispatcher):
    dispatcher = make_dispatcher(110)
    order = _order(amount=10.0, price=100.0, reduce_only=True)

    assert await dispatcher._apply_max_usd_cap(order) is None

    assert order.amount == 10.0


@pytest.mark.asyncio
async def test_with_no_stored_override_the_default_applies(make_dispatcher):
    dispatcher = make_dispatcher(None)  # nothing stored: the 1000.0 default
    big = _order(amount=20.0, price=100.0)  # $2,000
    small = _order(amount=5.0, price=100.0)  # $500

    assert await dispatcher._apply_max_usd_cap(big) is None
    assert big.amount == pytest.approx(10.0)
    assert await dispatcher._apply_max_usd_cap(small) is None
    assert small.amount == 5.0


@pytest.mark.asyncio
async def test_an_unpriced_entry_order_is_rejected_not_waved_through(make_dispatcher):
    dispatcher = make_dispatcher(110)
    order = _order(amount=1.0, price=None)
    dispatcher._emit_execution_event_from_order = AsyncMock()

    result = await dispatcher._apply_max_usd_cap(order)

    assert result["reason"] == "max_position_size_usd"


@pytest.mark.asyncio
async def test_the_clamp_runs_before_the_position_limit_checks(make_dispatcher):
    dispatcher = make_dispatcher(110)
    order = _order(amount=10.0, price=100.0)
    seen = []

    async def check(candidate):
        seen.append(candidate.amount)
        return False

    dispatcher.position_manager.check_position_limits = check
    dispatcher.position_manager.rejection_reason = "portfolio_exposure"
    dispatcher._emit_execution_event_from_order = AsyncMock()

    await dispatcher._execute_order_with_consensus(order)

    assert seen == [pytest.approx(1.1)]
