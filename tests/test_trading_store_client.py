from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from shared.trading_store_client import TradingStoreClient
from tradeengine.services.data_manager_client import APIError


@pytest.mark.asyncio
async def test_get_daily_pnl_uses_typed_endpoint_and_maps_404_to_none():
    client = TradingStoreClient()
    client.data_manager_client._client.request = AsyncMock(
        return_value={"date": "2026-09-25", "daily_pnl": -12.5}
    )

    assert await client.get_daily_pnl("2026-09-25") == -12.5
    client.data_manager_client._client.request.assert_awaited_once_with(
        "GET", "/api/v1/trading/daily-pnl/2026-09-25"
    )

    client.data_manager_client._client.request = AsyncMock(
        side_effect=APIError("missing", status_code=404)
    )
    assert await client.get_daily_pnl("2026-09-25") is None


@pytest.mark.asyncio
async def test_get_daily_pnl_raises_non_404_errors():
    client = TradingStoreClient()
    client.data_manager_client._client.request = AsyncMock(
        side_effect=APIError("unavailable", status_code=503)
    )

    with pytest.raises(APIError):
        await client.get_daily_pnl("2026-09-25")


@pytest.mark.asyncio
async def test_update_daily_pnl_uses_typed_endpoint_and_returns_failure():
    client = TradingStoreClient()
    request = AsyncMock()
    client.data_manager_client._client.request = request

    result = await client.update_daily_pnl("2026-09-25", -12.5)

    assert result.ok is True
    assert request.await_args.args == ("PUT", "/api/v1/trading/daily-pnl/2026-09-25")
    assert request.await_args.kwargs["json"]["daily_pnl"] == -12.5
    updated_at = request.await_args.kwargs["json"]["updated_at"]
    assert datetime.fromisoformat(updated_at).tzinfo is not None

    request.side_effect = APIError("unavailable", status_code=503)
    result = await client.update_daily_pnl("2026-09-25", -12.5)
    assert result.ok is False
    assert result.reason == "transient"
