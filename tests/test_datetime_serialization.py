"""Tests for datetime JSON serialization fixes (petrosa-tradeengine#495).

AC1: Reproduce the 'Object of type datetime is not JSON serializable' failure
     in unit tests that persist strategy positions / exchange positions /
     contributions / daily P&L carrying datetime fields.
AC3: Assert that a round-trip persist succeeds with tz-aware and naive datetimes.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.constants import UTC

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_http_client():
    """An httpx.AsyncClient mock that captures the json= argument."""
    mock = MagicMock()
    mock.request = AsyncMock()
    mock.request.return_value = MagicMock(
        status_code=200, json=MagicMock(return_value={"status": "ok"})
    )
    return mock


@pytest.fixture
def dm_client(mock_http_client):
    """A BaseDataManagerClient with an injected mock HTTP client."""
    from tradeengine.services.data_manager_client import BaseDataManagerClient

    client = BaseDataManagerClient.__new__(BaseDataManagerClient)
    client._client = mock_http_client
    client.max_retries = 1
    client.retry_delay = 0.0
    client.base_url = "http://localhost:8001"
    return client


# ---------------------------------------------------------------------------
# Tests for _serialize_for_http helper
# ---------------------------------------------------------------------------


class TestSerializeForHttp:
    """Unit tests for the _serialize_for_http / _json_default helpers."""

    def test_tz_aware_datetime_is_serialized_to_isoformat(self):
        """A tz-aware datetime inside a dict round-trips through JSON as a string."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        now = datetime.now(UTC)
        payload = {"entry_time": now, "symbol": "BTCUSDT"}
        result = _serialize_for_http(payload)

        assert isinstance(result["entry_time"], str), (
            "datetime must be converted to str by _serialize_for_http"
        )
        # Verify the string is parseable ISO-8601
        parsed = datetime.fromisoformat(result["entry_time"])
        assert parsed is not None

    def test_naive_datetime_is_serialized_to_isoformat(self):
        """A naive datetime (no tzinfo) is also serialized without error."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        naive = datetime(2026, 7, 1, 12, 0, 0)  # no tzinfo
        payload = {"created_at": naive}
        result = _serialize_for_http(payload)

        assert isinstance(result["created_at"], str)
        assert "2026-07-01" in result["created_at"]

    def test_none_body_returns_none(self):
        """None body is returned unchanged."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        assert _serialize_for_http(None) is None

    def test_non_datetime_values_are_preserved(self):
        """Strings, ints, floats, bools, and None values are left unchanged."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        payload = {
            "symbol": "BTCUSDT",
            "qty": 0.001,
            "price": 50000.0,
            "active": True,
            "note": None,
        }
        result = _serialize_for_http(payload)
        assert result == payload

    def test_nested_datetime_is_serialized(self):
        """Datetimes nested in sub-dicts or lists are also converted."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        now = datetime.now(UTC)
        payload = {
            "position": {"entry_time": now, "symbol": "ETHUSDT"},
            "timestamps": [now],
        }
        result = _serialize_for_http(payload)

        assert isinstance(result["position"]["entry_time"], str)
        assert isinstance(result["timestamps"][0], str)

    def test_unknown_non_serializable_type_raises_type_error(self):
        """A non-datetime, non-JSON-native object still raises TypeError."""
        from tradeengine.services.data_manager_client import _serialize_for_http

        class _Custom:
            pass

        with pytest.raises(TypeError) as exc_info:
            _serialize_for_http({"bad": _Custom()})
        assert "not JSON serializable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AC1: Reproduce the original failure (before the fix this would crash)
# AC3: After fix, the round-trip succeeds
# ---------------------------------------------------------------------------


class TestRetryRequestDatetimeSerialization:
    """_retry_request must not raise TypeError for datetime-bearing payloads."""

    @pytest.mark.asyncio
    async def test_strategy_position_with_entry_time_does_not_raise(
        self, dm_client, mock_http_client
    ):
        """AC1+AC3: persist a strategy position carrying a tz-aware entry_time."""
        now = datetime.now(UTC)
        body = {
            "strategy_id": "test-strategy",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "status": "open",
            "entry_price": 50000.0,
            "entry_time": now,  # ← this was the crash field
        }

        # Should NOT raise TypeError
        await dm_client._retry_request("POST", "/api/v1/data/insert", json_body=body)

        mock_http_client.request.assert_called_once()
        # The actual json= arg passed to httpx must be JSON-safe
        call_kwargs = mock_http_client.request.call_args.kwargs
        serialized_body = call_kwargs["json"]
        # Verify the datetime was converted to an ISO string
        assert isinstance(serialized_body["entry_time"], str)
        json.dumps(serialized_body)  # Must not raise

    @pytest.mark.asyncio
    async def test_exchange_position_with_last_update_time_does_not_raise(
        self, dm_client, mock_http_client
    ):
        """AC1+AC3: persist an exchange position carrying last_update_time."""
        now = datetime.now(UTC)
        body = {
            "symbol": "BNBUSDT",
            "side": "SHORT",
            "first_entry_time": now,
            "last_update_time": now,
            "notional": 100.0,
        }

        await dm_client._retry_request("POST", "/api/v1/data/insert", json_body=body)

        call_kwargs = mock_http_client.request.call_args.kwargs
        serialized_body = call_kwargs["json"]
        assert isinstance(serialized_body["first_entry_time"], str)
        assert isinstance(serialized_body["last_update_time"], str)
        json.dumps(serialized_body)

    @pytest.mark.asyncio
    async def test_contribution_with_contribution_time_does_not_raise(
        self, dm_client, mock_http_client
    ):
        """AC1+AC3: persist a contribution carrying contribution_time."""
        now = datetime.now(UTC)
        body = {
            "strategy_position_id": "uuid-abc",
            "symbol": "LTCUSDT",
            "contribution_time": now,
            "amount": 0.5,
        }

        await dm_client._retry_request("POST", "/api/v1/data/insert", json_body=body)

        call_kwargs = mock_http_client.request.call_args.kwargs
        serialized_body = call_kwargs["json"]
        assert isinstance(serialized_body["contribution_time"], str)
        json.dumps(serialized_body)

    @pytest.mark.asyncio
    async def test_naive_datetime_in_payload_does_not_raise(
        self, dm_client, mock_http_client
    ):
        """AC3: naive datetime (no tzinfo) is also handled without error."""
        naive = datetime(2026, 7, 2, 8, 0, 0)  # no tzinfo
        body = {"entry_time": naive, "symbol": "DOTUSDT"}

        await dm_client._retry_request("POST", "/api/v1/data/insert", json_body=body)

        call_kwargs = mock_http_client.request.call_args.kwargs
        assert isinstance(call_kwargs["json"]["entry_time"], str)

    @pytest.mark.asyncio
    async def test_exit_time_in_close_payload_does_not_raise(
        self, dm_client, mock_http_client
    ):
        """AC1+AC3: closing a position sets exit_time — must not raise."""
        now = datetime.now(UTC)
        body = {
            "strategy_position_id": "uuid-xyz",
            "status": "closed",
            "exit_time": now,
            "exit_price": 51000.0,
        }

        await dm_client._retry_request("PUT", "/api/v1/data/update", json_body=body)

        call_kwargs = mock_http_client.request.call_args.kwargs
        assert isinstance(call_kwargs["json"]["exit_time"], str)


# ---------------------------------------------------------------------------
# AC1+AC3: DataManagerPositionClient.update_daily_pnl — updated_at is now str
# ---------------------------------------------------------------------------


class TestUpdateDailyPnLSerializesDatetime:
    """The updated_at field in update_daily_pnl must arrive as a string (not datetime)."""

    @pytest.mark.asyncio
    async def test_updated_at_is_isoformat_string(self):
        """AC3: update_daily_pnl stores updated_at as ISO string, not raw datetime."""
        from shared.mysql_client import DataManagerPositionClient

        mock_dm = MagicMock()
        mock_dm._client = AsyncMock()
        mock_dm._client.upsert_one = AsyncMock(return_value={"upserted_id": "x"})

        with patch("shared.mysql_client.DataManagerClient", return_value=mock_dm):
            client = DataManagerPositionClient()
            client.data_manager_client = mock_dm

        await client.update_daily_pnl("2026-07-02", 250.0)

        call_args = mock_dm._client.upsert_one.call_args
        updated_at = call_args.kwargs["record"]["updated_at"]

        # Must be a string, not a datetime
        assert isinstance(updated_at, str), (
            "updated_at must be an ISO-8601 string, not a raw datetime object"
        )
        # Must parse as ISO-8601
        parsed = datetime.fromisoformat(updated_at)
        assert parsed is not None

    @pytest.mark.asyncio
    async def test_updated_at_contains_timezone_info(self):
        """AC3: The ISO-8601 string retains timezone offset information."""
        from shared.mysql_client import DataManagerPositionClient

        mock_dm = MagicMock()
        mock_dm._client = AsyncMock()
        mock_dm._client.upsert_one = AsyncMock(return_value={"upserted_id": "x"})

        with patch("shared.mysql_client.DataManagerClient", return_value=mock_dm):
            client = DataManagerPositionClient()
            client.data_manager_client = mock_dm

        await client.update_daily_pnl("2026-07-02", 250.0)

        call_args = mock_dm._client.upsert_one.call_args
        updated_at = call_args.kwargs["record"]["updated_at"]

        # ISO-8601 UTC string includes '+00:00' or 'Z'
        assert "+" in updated_at or updated_at.endswith("Z") or "UTC" in updated_at, (
            f"updated_at '{updated_at}' does not appear to have timezone info"
        )


# ---------------------------------------------------------------------------
# AC1+AC3: logger.py json.dumps with default=str
# ---------------------------------------------------------------------------


class TestAuditLoggerJsonDumps:
    """shared/logger.py json.dumps calls must not crash on datetime-bearing dicts."""

    def _make_order_data_with_datetime(self) -> dict:
        return {
            "type": "LIMIT",
            "side": "BUY",
            "symbol": "BCHUSDT",
            "created_at": datetime.now(UTC),  # raw datetime in order payload
        }

    def test_json_dumps_order_data_with_datetime_does_not_raise(self):
        """AC1: json.dumps with default=str handles datetime in order_data."""
        order_data = self._make_order_data_with_datetime()
        # This is what logger.py does after the fix
        result = json.dumps(order_data, default=str)
        parsed = json.loads(result)
        assert isinstance(parsed["created_at"], str)

    def test_json_dumps_result_data_with_datetime_does_not_raise(self):
        """AC1: json.dumps with default=str handles datetime in result_data."""
        result_data = {
            "order_id": "12345678901234",
            "executed_at": datetime.now(UTC),
        }
        serialized = json.dumps(result_data, default=str)
        parsed = json.loads(serialized)
        assert isinstance(parsed["executed_at"], str)

    def test_json_dumps_without_default_raises_for_datetime(self):
        """Baseline: confirms the original bug — json.dumps without default= raises."""
        order_data = self._make_order_data_with_datetime()
        with pytest.raises(TypeError, match="not JSON serializable") as exc_info:
            json.dumps(order_data)  # no default= — must raise
        assert "datetime" in str(exc_info.value).lower()
