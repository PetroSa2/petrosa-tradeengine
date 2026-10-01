from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tradeengine.services.exchange_daily_publisher import ExchangeDailyPublisher


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def futures_income_history(self, **kwargs):
        self.calls.append(kwargs)
        start = kwargs["startTime"]
        return [row for row in self.rows if row["time"] >= start][:1000]

    def futures_account(self):
        return {"totalWalletBalance": "12.34567891"}

    def futures_position_information(self):
        return [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "1",
                "entryPrice": "10",
                "markPrice": "11",
                "unRealizedProfit": "1",
            },
            {
                "symbol": "BTCUSDT",
                "positionSide": "SHORT",
                "positionAmt": "-2",
                "entryPrice": "12",
                "markPrice": "11",
                "unRealizedProfit": "2",
            },
        ]


class FakeDataManager:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"ok": True}


@pytest.mark.asyncio
async def test_paginates_and_preserves_decimal_strings():
    rows = [
        {
            "time": 1_735_689_600_000 + i,
            "symbol": "BTCUSDT",
            "asset": "USDT",
            "incomeType": "COMMISSION",
            "income": "-0.00000001",
        }
        for i in range(2300)
    ]
    exchange = SimpleNamespace(client=FakeClient(rows), rate_monitor=None)
    publisher = ExchangeDailyPublisher(exchange, FakeDataManager())
    collected = await publisher.income_rows(1_735_689_600_000, 1_735_776_000_000)
    assert len(collected) == 2300
    assert len(exchange.client.calls) == 3
    payload = publisher.aggregate(collected, "2025-01-01")
    assert payload["rows"][0]["income_by_type"]["COMMISSION"] == "-0.00002300"


@pytest.mark.asyncio
async def test_exchange_page_error_does_not_put():
    class Broken(FakeClient):
        def futures_income_history(self, **kwargs):
            raise RuntimeError("page failed")

    data_manager = FakeDataManager()
    publisher = ExchangeDailyPublisher(
        SimpleNamespace(client=Broken([]), rate_monitor=None), data_manager
    )
    result = await publisher.publish_day("2025-01-01")
    assert result["result"] == "exchange_error"
    assert data_manager.calls == []


@pytest.mark.asyncio
async def test_positions_keep_hedge_sides():
    data_manager = FakeDataManager()
    publisher = ExchangeDailyPublisher(
        SimpleNamespace(client=FakeClient([]), rate_monitor=None), data_manager
    )
    await publisher._positions_snapshot(123)
    assert len(data_manager.calls[0][2]["json"]["rows"]) == 2
