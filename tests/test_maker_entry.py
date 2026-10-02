from __future__ import annotations

import pytest

from contracts.order import TradeOrder
from tradeengine.maker_entry import MakerEntryExecutor


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeExchange:
    def __init__(self, statuses: list[dict[str, object]]) -> None:
        self.statuses = statuses
        self.orders: list[TradeOrder] = []
        self.cancelled = False

    async def get_best_entry_price(self, symbol: str, side: str) -> float:
        return 99.0 if side == "buy" else 101.0

    async def execute(self, order: TradeOrder) -> dict[str, object]:
        self.orders.append(order)
        return {"order_id": len(self.orders), "status": "new", "amount": 0.0}

    async def get_order_status(self, symbol: str, order_id: int) -> dict[str, object]:
        return self.statuses.pop(0)

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, object]:
        self.cancelled = True
        return {"order_id": order_id, "status": "cancelled", "amount": 0.0}


class RejectingExchange(FakeExchange):
    async def execute(self, order: TradeOrder) -> dict[str, object]:
        self.orders.append(order)
        return {"status": "rejected", "error": "post-only would cross", "amount": 0.0}


def make_order(amount: float = 1.0) -> TradeOrder:
    return TradeOrder(symbol="BTCUSDT", side="buy", type="market", amount=amount)


@pytest.mark.asyncio
async def test_full_fill_uses_gtx_and_never_falls_back() -> None:
    clock = FakeClock()
    exchange = FakeExchange(
        [{"order_id": 1, "status": "filled", "amount": 1.0, "fill_price": 99.0}]
    )

    result = await MakerEntryExecutor(
        exchange, 5, "market", clock.now, clock.sleep
    ).execute(make_order())

    assert result["entry_mode"] == "maker"
    assert len(exchange.orders) == 1
    assert exchange.orders[0].time_in_force == "GTX"
    assert exchange.orders[0].target_price == 99.0


@pytest.mark.asyncio
async def test_partial_timeout_fallback_covers_only_remainder() -> None:
    clock = FakeClock()
    exchange = FakeExchange(
        [
            {
                "order_id": 1,
                "status": "partially_filled",
                "amount": 0.4,
                "fill_price": 99.0,
            }
        ]
    )

    result = await MakerEntryExecutor(
        exchange, 0.1, "market", clock.now, clock.sleep
    ).execute(make_order())

    assert result["entry_mode"] == "maker_fallback_market"
    assert len(exchange.orders) == 2
    assert exchange.orders[1].type == "market"
    assert exchange.orders[1].amount == 0.6


@pytest.mark.asyncio
async def test_zero_fill_without_fallback_is_cancelled() -> None:
    clock = FakeClock()
    exchange = FakeExchange([{"order_id": 1, "status": "new", "amount": 0.0}])

    result = await MakerEntryExecutor(
        exchange, 0.1, "none", clock.now, clock.sleep
    ).execute(make_order())

    assert result["entry_mode"] == "maker_unfilled"
    assert result["maker_unfilled"] is True
    assert exchange.cancelled is True


@pytest.mark.asyncio
async def test_crossing_rejection_is_not_retried() -> None:
    clock = FakeClock()
    exchange = RejectingExchange([])

    result = await MakerEntryExecutor(
        exchange, 5, "none", clock.now, clock.sleep
    ).execute(make_order())

    assert result["post_only_rejected"] is True
    assert len(exchange.orders) == 1
    assert clock.value == 0.0
