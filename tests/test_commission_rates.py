"""The commission rate per symbol is read from the exchange and exposed on /state (te#729)."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.commission_rates import (
    FALLBACK_MAKER_RATE,
    FALLBACK_TAKER_RATE,
    FEE_BURN_DISCOUNT,
    CommissionRates,
)
from tradeengine.exchange.binance import BinanceFuturesExchange


def _rates(exchange=None, symbols=None):
    return CommissionRates(exchange or MagicMock(), symbols or ["BTCUSDT"])


def _exchange(*results):
    exchange = MagicMock()
    exchange.get_commission_rate = AsyncMock(side_effect=list(results))
    return exchange


def test_before_any_read_the_rates_are_a_labelled_fallback():
    rate = _rates().get("BTCUSDT")

    assert rate["source"] == "fallback"
    assert rate["taker_rate"] == FALLBACK_TAKER_RATE == 0.0005
    assert rate["maker_rate"] == FALLBACK_MAKER_RATE == 0.0002
    assert rate["fetched_at"] is None


@pytest.mark.asyncio
async def test_a_successful_read_reports_the_exchange_rate_with_its_source():
    rates = _rates(_exchange((0.00016, 0.0004, False)))

    assert await rates.refresh("BTCUSDT") is True

    rate = rates.get("BTCUSDT")
    assert rate["source"] == "exchange"
    assert rate["maker_rate"] == pytest.approx(0.00016)
    assert rate["taker_rate"] == pytest.approx(0.0004)
    assert rate["fetched_at"] is not None
    assert rate["fee_burn"] is False


@pytest.mark.asyncio
async def test_the_fee_asset_discount_applies_when_the_account_has_it_on():
    rates = _rates(_exchange((0.0002, 0.0005, True)))

    await rates.refresh("BTCUSDT")

    rate = rates.get("BTCUSDT")
    assert rate["taker_rate"] == pytest.approx(0.0005 * (1 - FEE_BURN_DISCOUNT))
    assert rate["maker_rate"] == pytest.approx(0.0002 * (1 - FEE_BURN_DISCOUNT))
    assert rate["fee_burn"] is True


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_last_good_value_and_warns(caplog):
    rates = _rates(_exchange((0.0001, 0.0003, False), RuntimeError("exchange down")))
    await rates.refresh("BTCUSDT")
    good = rates.get("BTCUSDT")

    with caplog.at_level(logging.WARNING):
        assert await rates.refresh("BTCUSDT") is False

    assert rates.get("BTCUSDT") == good
    assert "Commission rate refresh failed for BTCUSDT" in caplog.text


@pytest.mark.asyncio
async def test_a_read_that_never_succeeded_stays_on_the_fallback():
    rates = _rates(_exchange(RuntimeError("no keys")))

    assert await rates.refresh("BTCUSDT") is False

    assert rates.get("BTCUSDT")["source"] == "fallback"


@pytest.mark.asyncio
async def test_a_symbol_without_a_value_is_read_once_then_backed_off():
    exchange = _exchange(RuntimeError("down"), (0.0001, 0.0003, False))
    rates = _rates(exchange)

    await rates.ensure("ETHUSDT")
    await rates.ensure("ETHUSDT")  # within the retry window: no second call

    assert exchange.get_commission_rate.await_count == 1
    assert rates.get("ETHUSDT")["source"] == "fallback"


@pytest.mark.asyncio
async def test_the_loop_reads_every_symbol_at_startup_and_again_each_interval():
    exchange = MagicMock()
    exchange.get_commission_rate = AsyncMock(return_value=(0.0001, 0.0003, False))
    rates = CommissionRates(exchange, ["BTCUSDT", "ETHUSDT"])

    task = asyncio.create_task(rates.run(interval_seconds=0.02))
    await asyncio.sleep(0.07)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task

    assert cancelled.type is asyncio.CancelledError
    assert (
        exchange.get_commission_rate.await_count >= 4
    )  # two symbols, at least two rounds
    assert rates.get("ETHUSDT")["source"] == "exchange"


@pytest.mark.asyncio
async def test_state_reports_the_commission_of_the_requested_symbol():
    import tradeengine.api as api

    rates = _rates(_exchange((0.00016, 0.0004, False)))
    await rates.refresh("BTCUSDT")
    dispatcher = MagicMock()
    dispatcher.get_cio_state.return_value = {"risk_limits": {}}
    dispatcher.resolve_risk_cap = AsyncMock(
        return_value={"probe_mode": False, "max_position_size_usd": 1000.0}
    )
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(api, "commission_rates", rates),
    ):
        state = await api.get_state(symbol="BTCUSDT", side=None)

    assert state["commission"]["source"] == "exchange"
    assert state["commission"]["taker_rate"] == pytest.approx(0.0004)
    assert state["commission"]["maker_rate"] == pytest.approx(0.00016)
    assert set(state["commission"]) >= {
        "taker_rate",
        "maker_rate",
        "source",
        "fetched_at",
    }


@pytest.mark.asyncio
async def test_the_exchange_client_call_parses_rates_and_the_fee_burn_flag():
    exchange = BinanceFuturesExchange()
    exchange.initialized = True
    exchange.client = MagicMock()
    exchange.client.futures_commission_rate.return_value = {
        "symbol": "BTCUSDT",
        "makerCommissionRate": "0.000200",
        "takerCommissionRate": "0.000500",
    }
    exchange.client.futures_v1_get_fee_burn.return_value = {"feeBurn": True}

    maker, taker, fee_burn = await exchange.get_commission_rate("BTCUSDT")

    assert (maker, taker, fee_burn) == (0.0002, 0.0005, True)
    exchange.client.futures_commission_rate.assert_called_once_with(symbol="BTCUSDT")


@pytest.mark.asyncio
async def test_an_unreadable_fee_burn_setting_means_no_discount():
    exchange = BinanceFuturesExchange()
    exchange.initialized = True
    exchange.client = MagicMock()
    exchange.client.futures_commission_rate.return_value = {
        "makerCommissionRate": "0.0002",
        "takerCommissionRate": "0.0005",
    }
    exchange.client.futures_v1_get_fee_burn.side_effect = RuntimeError("no permission")

    _, _, fee_burn = await exchange.get_commission_rate("BTCUSDT")

    assert fee_burn is False
