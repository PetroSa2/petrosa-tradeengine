from tradeengine.hedge_netting import decide, offset_notional


def _position(side: str, quantity: float, strategy_id: str = "owner") -> dict:
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "entry_quantity": quantity,
        "entry_price": 100.0,
        "strategy_id": strategy_id,
        "strategy_position_id": "position-a",
        "status": "open",
    }


def test_allow_both_preserves_opposite_signal() -> None:
    decision = decide(
        policy="allow_both",
        symbol="BTCUSDT",
        action="sell",
        quantity=0.4,
        positions=[_position("LONG", 1.0)],
    )

    assert decision.policy == "allow_both"
    assert decision.quantity == 0.0


def test_net_reduces_owner_and_records_residual() -> None:
    decision = decide(
        policy="net",
        symbol="BTCUSDT",
        action="sell",
        quantity=1.5,
        positions=[_position("LONG", 1.0, "strategy-a")],
    )

    assert decision.position_side == "LONG"
    assert decision.owner_strategy_id == "strategy-a"
    assert decision.quantity == 1.0
    assert decision.skipped_quantity == 0.5


def test_block_opposite_returns_reasonable_target_side() -> None:
    decision = decide(
        policy="block_opposite",
        symbol="BTCUSDT",
        action="buy",
        quantity=0.4,
        positions=[_position("SHORT", 1.0)],
    )

    assert decision.position_side == "SHORT"


def test_offset_notional_uses_smaller_held_leg() -> None:
    assert (
        offset_notional([_position("LONG", 1.0), _position("SHORT", 0.4)], "BTCUSDT")
        == 40.0
    )
