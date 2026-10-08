from types import SimpleNamespace

import pytest

from tradeengine.services.cost_telemetry import (
    build_cost_fields,
    cost_signal_ratio,
    planned_move_bp,
)


def order(**kwargs):
    values = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "type": "market",
        "amount": 1,
        "target_price": None,
        "stop_loss": None,
        "take_profit": None,
        "reduce_only": False,
        "strategy_metadata": {"signal_price": 100},
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("side", "fill", "expected"),
    [
        ("buy", 100.05, 5.0),
        ("sell", 99.95, 5.0),
        ("buy", 99.95, -5.0),
        ("sell", 100.05, -5.0),
    ],
)
def test_slippage_is_positive_when_fill_is_worse(side, fill, expected):
    fields = build_cost_fields(
        order(side=side),
        {"fill_price": fill, "amount": 1, "fees": 0, "fee_asset": "USDT"},
    )
    assert fields["slippage_bp"] == pytest.approx(expected)


def test_non_quote_fee_needs_conversion_without_division():
    fields = build_cost_fields(
        order(), {"fill_price": 100, "amount": 1, "fees": 1, "fee_asset": "BNB"}
    )
    assert fields["fee_status"] == "needs_conversion"
    assert fields["fee_bp"] is None


def test_missing_fee_is_explicit_unknown_zero():
    fields = build_cost_fields(order(), {"fill_price": 100, "amount": 1})
    assert fields["fee"] == 0.0
    assert fields["fee_status"] == "unknown"


def test_role_intended_prices():
    assert (
        build_cost_fields(
            order(type="stop", stop_loss=95), {"fill_price": 94, "amount": 1, "fees": 0}
        )["intended_price_source"]
        == "stop_trigger"
    )
    assert (
        build_cost_fields(
            order(type="take_profit", take_profit=105),
            {"fill_price": 104, "amount": 1, "fees": 0},
        )["intended_price_source"]
        == "take_profit_trigger"
    )
    assert (
        build_cost_fields(
            order(reduce_only=True),
            {"fill_price": 100, "amount": 1, "fees": 0},
            mark_price=101,
        )["intended_price_source"]
        == "manual_close_mark"
    )


def test_planned_move_uses_absolute_entry_to_target_distance():
    assert planned_move_bp(
        order(strategy_metadata={"signal_price": 100}, take_profit=100.12)
    ) == pytest.approx(12.0)


def test_cost_signal_ratio_is_cost_over_signal_move():
    assert cost_signal_ratio(12, 8) == pytest.approx(2 / 3)
    assert cost_signal_ratio(0, 8) is None
