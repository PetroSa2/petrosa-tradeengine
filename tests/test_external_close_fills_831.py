"""Attribution of exchange closing fills to externally closed positions (#831).

Fixtures are shaped like Binance USD-M ``GET /fapi/v1/userTrades`` rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tradeengine.external_close_fills import (
    PNL_SOURCE_EXCHANGE_FILLS,
    PNL_SOURCE_MISSING,
    PNL_SOURCE_UNATTRIBUTED,
    attribute_closing_fills,
    closing_fill_candidates,
    missing_closure,
)

ENTRY = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
ENTRY_MS = int(ENTRY.timestamp() * 1000)


def _pos(**overrides):
    position = {
        "strategy_position_id": "sp-1",
        "strategy_id": "iceberg_detector",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_quantity": 0.010,
        "exit_quantity": 0,
        "entry_price": 100000.0,
        "entry_time": ENTRY,
        "entry_order_id": "111",
    }
    position.update(overrides)
    return position


def _trade(
    trade_id,
    order_id,
    side,
    qty,
    price,
    pnl="0",
    commission="0.02",
    offset_ms=60_000,
    position_side="BOTH",
):
    return {
        "symbol": "BTCUSDT",
        "id": trade_id,
        "orderId": order_id,
        "side": side,
        "positionSide": position_side,
        "price": price,
        "qty": qty,
        "realizedPnl": pnl,
        "quoteQty": str(Decimal(price) * Decimal(qty)),
        "commission": commission,
        "commissionAsset": "USDT",
        "time": ENTRY_MS + offset_ms,
        "buyer": side == "BUY",
        "maker": False,
    }


ENTRY_FILL = _trade(1, 111, "BUY", "0.010", "100000", offset_ms=0)


def test_closing_fills_set_exit_price_pnl_and_commission():
    trades = [
        ENTRY_FILL,
        _trade(2, 222, "SELL", "0.004", "101000", pnl="4.0", commission="0.0404"),
        _trade(
            3,
            223,
            "SELL",
            "0.006",
            "102000",
            pnl="12.0",
            commission="0.0612",
            offset_ms=120_000,
        ),
    ]
    closure = attribute_closing_fills(_pos(), trades, other_positions=[])

    assert closure["pnl_source"] == PNL_SOURCE_EXCHANGE_FILLS
    # quantity-weighted: (0.004*101000 + 0.006*102000) / 0.010
    assert closure["exit_price"] == Decimal("101600")
    assert closure["exit_quantity"] == Decimal("0.010")
    assert closure["realized_pnl"] == Decimal("16.0")
    assert closure["commission_by_asset"] == {"USDT": Decimal("0.1016")}
    assert closure["exit_order_ids"] == ["222", "223"]
    assert closure["exit_time"] == datetime.fromtimestamp(
        (ENTRY_MS + 120_000) / 1000, tz=UTC
    )
    assert [f["trade_id"] for f in closure["fills"]] == [2, 3]


def test_short_is_closed_by_buy_fills():
    trades = [
        _trade(1, 111, "SELL", "0.010", "100000", offset_ms=0),
        _trade(2, 333, "BUY", "0.010", "99000", pnl="10.0"),
    ]
    closure = attribute_closing_fills(_pos(side="SHORT"), trades, other_positions=[])
    assert closure["pnl_source"] == PNL_SOURCE_EXCHANGE_FILLS
    assert closure["realized_pnl"] == Decimal("10.0")
    assert closure["exit_price"] == Decimal("99000")


def test_partially_closed_position_matches_remaining_quantity():
    trades = [_trade(2, 222, "SELL", "0.006", "101000", pnl="6.0")]
    closure = attribute_closing_fills(
        _pos(exit_quantity=0.004), trades, other_positions=[]
    )
    assert closure["pnl_source"] == PNL_SOURCE_EXCHANGE_FILLS
    assert closure["exit_quantity"] == Decimal("0.006")


def test_shared_symbol_side_is_unattributed_with_candidates_and_no_pnl():
    trades = [ENTRY_FILL, _trade(2, 222, "SELL", "0.010", "101000", pnl="10.0")]
    other = _pos(strategy_position_id="sp-2", strategy_id="spread_liquidity")
    closure = attribute_closing_fills(_pos(), trades, other_positions=[other])

    assert closure["pnl_source"] == PNL_SOURCE_UNATTRIBUTED
    assert closure["reason"] == "symbol_side_shared"
    assert closure["sharing_positions"] == ["sp-2"]
    assert [f["trade_id"] for f in closure["candidate_fills"]] == [2]
    assert "realized_pnl" not in closure
    assert "exit_price" not in closure


def test_quantity_mismatch_is_unattributed():
    trades = [_trade(2, 222, "SELL", "0.015", "101000", pnl="15.0")]
    closure = attribute_closing_fills(_pos(), trades, other_positions=[])
    assert closure["pnl_source"] == PNL_SOURCE_UNATTRIBUTED
    assert closure["reason"] == "quantity_mismatch"
    assert closure["remaining_quantity"] == "0.01"
    assert closure["closing_quantity"] == "0.015"
    assert "realized_pnl" not in closure


def test_no_closing_fills_is_unattributed():
    closure = attribute_closing_fills(_pos(), [ENTRY_FILL], other_positions=[])
    assert closure["pnl_source"] == PNL_SOURCE_UNATTRIBUTED
    assert closure["reason"] == "no_closing_fills"
    assert closure["candidate_fills"] == []


def test_other_side_and_unrelated_positions_do_not_block_attribution():
    trades = [_trade(2, 222, "SELL", "0.010", "101000", pnl="10.0")]
    others = [
        _pos(strategy_position_id="sp-short", side="SHORT"),
        _pos(strategy_position_id="sp-eth", symbol="ETHUSDT"),
        _pos(),  # the position itself is never "another" position
    ]
    closure = attribute_closing_fills(_pos(), trades, other_positions=others)
    assert closure["pnl_source"] == PNL_SOURCE_EXCHANGE_FILLS


def test_candidates_exclude_fills_before_entry_entry_order_and_wrong_hedge_side():
    trades = [
        _trade(1, 222, "SELL", "0.010", "99000", offset_ms=-60_000),
        _trade(2, 111, "SELL", "0.010", "99000"),
        _trade(3, 224, "SELL", "0.010", "99000", position_side="SHORT"),
        _trade(4, 225, "SELL", "0.010", "99000", position_side="LONG"),
        {"symbol": "BTCUSDT", "id": 5, "side": "SELL", "time": ENTRY_MS + 1},
    ]
    assert [t["id"] for t in closing_fill_candidates(_pos(), trades)] == [4]


def test_iso_string_entry_time_is_accepted():
    trades = [_trade(2, 222, "SELL", "0.010", "101000", pnl="10.0")]
    closure = attribute_closing_fills(
        _pos(entry_time=ENTRY.isoformat()), trades, other_positions=[]
    )
    assert closure["pnl_source"] == PNL_SOURCE_EXCHANGE_FILLS


def test_missing_closure_has_no_pnl():
    closure = missing_closure("fetch_failed")
    assert closure == {"pnl_source": PNL_SOURCE_MISSING, "reason": "fetch_failed"}
