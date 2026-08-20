"""Tests for issue #550 acceptance criteria.

#550: in-memory ``active_oco_pairs`` diverges from Binance truth.

Covered here:
- AC-2 (consult exchange truth before placing): when Binance already holds a
  reduceOnly STOP+TP pair for (symbol, positionSide) AND the in-memory map is
  empty, ``place_oco_orders`` is a no-op (skipped_exchange_pair_exists).
- Negative control: an empty exchange (no algo orders) still allows placement.
- Gauge no-undercount: ``_sync_oco_pairs_gauge`` emits one row per covered
  (symbol, position_side) after reconcile so the gauge cannot silently
  undercount post-restart.
- Best-effort: a failing openAlgoOrders lookup falls through to placement
  rather than dropping a real protective pair.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import OCOManager


def _make_oco_manager(algo_orders: list | Exception | None = None) -> OCOManager:
    exchange = MagicMock()
    exchange.client = MagicMock()
    if isinstance(algo_orders, Exception):
        exchange.get_open_algo_orders = AsyncMock(side_effect=algo_orders)
    else:
        exchange.get_open_algo_orders = AsyncMock(return_value=algo_orders or [])

    _exec_call_count = [0]

    async def _fake_execute(order):
        _exec_call_count[0] += 1
        return {
            "order_id": f"order_{_exec_call_count[0]}",
            "algoId": f"order_{_exec_call_count[0]}",
            "status": "NEW",
        }

    exchange.execute = _fake_execute

    mgr = OCOManager(exchange=exchange, logger=logging.getLogger("test-550"))
    return mgr


def _protective_pair(symbol="BTCUSDT", position_side="LONG") -> list[dict]:
    return [
        {
            "symbol": symbol,
            "positionSide": position_side,
            "type": "STOP_MARKET",
            "algoId": "algo-sl-1",
            "closePosition": True,
        },
        {
            "symbol": symbol,
            "positionSide": position_side,
            "type": "TAKE_PROFIT_MARKET",
            "algoId": "algo-tp-1",
            "closePosition": True,
        },
    ]


class TestAC2ExchangeTruthDedup:
    @pytest.mark.asyncio
    async def test_covered_on_exchange_empty_map_is_noop(self):
        """Mandatory unit AC: covered position on the mocked exchange + empty
        in-memory map => place_oco_orders is a no-op."""
        mgr = _make_oco_manager(algo_orders=_protective_pair())
        assert mgr.active_oco_pairs == {}

        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
            strategy_position_id="strat_a",
        )

        assert result.get("status") == "skipped_exchange_pair_exists"
        assert result.get("sl_order_id") is None
        assert result.get("tp_order_id") is None
        # no local tracking created for the skipped placement
        assert mgr.active_oco_pairs.get("BTCUSDT_LONG", []) == []

    @pytest.mark.asyncio
    async def test_empty_exchange_still_places(self):
        mgr = _make_oco_manager(algo_orders=[])
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        assert result.get("status") != "skipped_exchange_pair_exists"

    @pytest.mark.asyncio
    async def test_only_stop_on_exchange_still_places(self):
        """Half-coverage (STOP only, no TP) must NOT be treated as a full
        pair — the missing leg still needs arming."""
        half = [_protective_pair()[0]]  # STOP only
        mgr = _make_oco_manager(algo_orders=half)
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        assert result.get("status") != "skipped_exchange_pair_exists"

    @pytest.mark.asyncio
    async def test_other_position_side_does_not_dedup(self):
        """A SHORT pair on the exchange must not skip a LONG placement."""
        mgr = _make_oco_manager(algo_orders=_protective_pair(position_side="SHORT"))
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        assert result.get("status") != "skipped_exchange_pair_exists"

    @pytest.mark.asyncio
    async def test_lookup_failure_falls_through_to_placement(self):
        mgr = _make_oco_manager(algo_orders=RuntimeError("binance down"))
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        assert result.get("status") != "skipped_exchange_pair_exists"

    @pytest.mark.asyncio
    async def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("TE_OCO_EXCHANGE_TRUTH_DEDUP", "0")
        mgr = _make_oco_manager(algo_orders=_protective_pair())
        result = await mgr.place_oco_orders(
            position_id="pos1",
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,
            stop_loss_price=48000.0,
            take_profit_price=52000.0,
        )
        assert result.get("status") != "skipped_exchange_pair_exists"


class TestExchangeHasProtectivePair:
    @pytest.mark.asyncio
    async def test_true_when_both_legs_present(self):
        mgr = _make_oco_manager(algo_orders=_protective_pair())
        assert await mgr._exchange_has_protective_pair("BTCUSDT", "LONG") is True

    @pytest.mark.asyncio
    async def test_false_when_no_orders(self):
        mgr = _make_oco_manager(algo_orders=[])
        assert await mgr._exchange_has_protective_pair("BTCUSDT", "LONG") is False

    @pytest.mark.asyncio
    async def test_false_when_only_one_leg(self):
        mgr = _make_oco_manager(algo_orders=[_protective_pair()[1]])  # TP only
        assert await mgr._exchange_has_protective_pair("BTCUSDT", "LONG") is False

    @pytest.mark.asyncio
    async def test_both_position_side_matches_one_way(self):
        both = _protective_pair(position_side="BOTH")
        mgr = _make_oco_manager(algo_orders=both)
        assert await mgr._exchange_has_protective_pair("BTCUSDT", "LONG") is True


class TestGaugeNoUndercount:
    def test_sync_gauge_emits_row_per_covered_position(self):
        """AC: gauge reflects exchange truth after reconcile (no undercount)."""
        mgr = _make_oco_manager()
        mgr.active_oco_pairs = {
            "BTCUSDT_LONG": [
                {"symbol": "BTCUSDT", "position_side": "LONG", "status": "active"}
            ],
            "ETHUSDT_SHORT": [
                {"symbol": "ETHUSDT", "position_side": "SHORT", "status": "active"}
            ],
            "ADAUSDT_LONG": [
                {"symbol": "ADAUSDT", "position_side": "LONG", "status": "completed"}
            ],
        }

        from tradeengine.metrics import active_oco_pairs_per_position

        mgr._sync_oco_pairs_gauge()

        btc = active_oco_pairs_per_position.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        )._value.get()
        eth = active_oco_pairs_per_position.labels(
            symbol="ETHUSDT", position_side="SHORT", exchange="binance"
        )._value.get()
        assert btc == 1
        assert eth == 1

    def test_sync_gauge_skips_when_no_active_entries(self):
        mgr = _make_oco_manager()
        mgr.active_oco_pairs = {
            "XRPUSDT_LONG": [
                {"symbol": "XRPUSDT", "position_side": "LONG", "status": "completed"}
            ],
        }
        # must not raise and must not emit a row for a non-active key
        mgr._sync_oco_pairs_gauge()
        from tradeengine.metrics import active_oco_pairs_per_position

        xrp = active_oco_pairs_per_position.labels(
            symbol="XRPUSDT", position_side="LONG", exchange="binance"
        )._value.get()
        assert xrp == 0
