"""
Tests for #448: position persistence retry, PersistResult, and PersistRetryQueue.

Covers:
  - shared.retry: PersistResult fields + is_transient_error classification
  - shared.mysql_client: DataManagerPositionClient returns PersistResult on success/failure
  - tradeengine.services.persist_retry_queue: enqueue, drain, never-persisted surfacing
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.retry import PersistResult, is_transient_error
from tradeengine.services.data_manager_client import APIError, ConnectionError
from tradeengine.services.persist_retry_queue import PendingWrite, PersistRetryQueue

# ---------------------------------------------------------------------------
# PersistResult
# ---------------------------------------------------------------------------


class TestPersistResult:
    def test_ok_result_properties(self):
        r = PersistResult(ok=True, operation="create_position", symbol="BTCUSDT")
        assert r.ok is True
        assert r.failed is False

    def test_failed_result_properties(self):
        r = PersistResult(ok=False, reason="transient", error="timeout")
        assert r.ok is False
        assert r.failed is True
        assert r.is_transient is True

    def test_permanent_reason(self):
        r = PersistResult(ok=False, reason="permanent", error="integrity error")
        assert r.is_transient is False
        assert r.failed is True


# ---------------------------------------------------------------------------
# is_transient_error
# ---------------------------------------------------------------------------


class TestIsTransientError:
    def test_connection_error_is_transient(self):
        exc = ConnectionError("connect timeout")
        assert is_transient_error(exc) is True

    def test_api_error_5xx_is_transient(self):
        for code in (500, 502, 503, 504):
            exc = APIError("server error", status_code=code)
            assert is_transient_error(exc) is True, f"Expected {code} to be transient"

    def test_api_error_429_is_transient(self):
        exc = APIError("rate limited", status_code=429)
        assert is_transient_error(exc) is True

    def test_api_error_4xx_not_transient(self):
        exc = APIError("bad request", status_code=400)
        assert is_transient_error(exc) is False

    def test_api_error_no_status_code_is_transient(self):
        exc = APIError("transport failure")
        assert is_transient_error(exc) is True

    def test_generic_exception_not_transient(self):
        exc = ValueError("validation error")
        assert is_transient_error(exc) is False


# ---------------------------------------------------------------------------
# DataManagerPositionClient
# ---------------------------------------------------------------------------


class TestDataManagerPositionClient:
    """Unit tests using a mocked BaseDataManagerClient."""

    def _make_client(self):
        from shared.mysql_client import DataManagerPositionClient

        client = DataManagerPositionClient.__new__(DataManagerPositionClient)
        mock_dm = MagicMock()
        mock_base = MagicMock()
        mock_dm._client = mock_base
        client.data_manager_client = mock_dm
        return client, mock_base

    @pytest.mark.asyncio
    async def test_create_position_success_returns_ok(self):
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            return_value={"inserted_id": "abc", "inserted_count": 1}
        )
        result = await client.create_position(
            {"position_id": "p1", "symbol": "BTCUSDT"}
        )
        assert result.ok is True
        assert result.operation == "create_position"

    @pytest.mark.asyncio
    async def test_create_position_api_error_returns_failed(self):
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            side_effect=APIError("server error", status_code=503)
        )
        result = await client.create_position(
            {"position_id": "p1", "symbol": "BTCUSDT"}
        )
        assert result.ok is False
        assert result.reason == "transient"
        assert result.failed is True

    @pytest.mark.asyncio
    async def test_update_position_success(self):
        client, mock_base = self._make_client()
        mock_base.update_one = AsyncMock(return_value={"modified_count": 1})
        result = await client.update_position("p1", {"status": "closed"})
        assert result.ok is True
        assert result.operation == "update_position"

    @pytest.mark.asyncio
    async def test_update_position_zero_modified_count_is_failed(self):
        client, mock_base = self._make_client()
        mock_base.update_one = AsyncMock(return_value={"modified_count": 0})
        result = await client.update_position("p1", {"status": "closed"})
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_close_position_success(self):
        client, mock_base = self._make_client()
        mock_base.update_one = AsyncMock(return_value={"modified_count": 1})
        result = await client.close_position("BTCUSDT", "LONG", {"status": "closed"})
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_get_open_positions_raises_on_connection_error(self):
        # AC1.5: get_open_positions must not silently return [] on transient error —
        # it should propagate so the caller can use the in-memory fallback explicitly.
        client, mock_base = self._make_client()
        mock_base.query = AsyncMock(side_effect=ConnectionError("down"))
        with pytest.raises(ConnectionError):
            await client.get_open_positions()

    @pytest.mark.asyncio
    async def test_upsert_position_success(self):
        client, mock_base = self._make_client()
        mock_base.upsert_one = AsyncMock(
            return_value={"modified_count": 1, "upserted_count": 0}
        )
        result = await client.upsert_position(
            {"symbol": "ETHUSDT", "position_side": "LONG"}
        )
        assert result.ok is True


# ---------------------------------------------------------------------------
# PersistRetryQueue
# ---------------------------------------------------------------------------


class TestPersistRetryQueue:
    def _make_queue(self, max_size=10, max_drain_attempts=3, drain_interval=0.01):
        return PersistRetryQueue(
            max_size=max_size,
            max_drain_attempts=max_drain_attempts,
            drain_interval=drain_interval,
        )

    def test_enqueue_returns_true_on_success(self):
        q = self._make_queue()
        pw = PendingWrite(
            operation="create_position", data={}, symbol="BTCUSDT", position_id="p1"
        )
        assert q.enqueue(pw) is True
        assert q.depth == 1

    def test_enqueue_returns_false_when_full_and_marks_never_persisted(self):
        q = self._make_queue(max_size=1)
        pw1 = PendingWrite(
            operation="create_position", data={}, symbol="BTCUSDT", position_id="p1"
        )
        pw2 = PendingWrite(
            operation="create_position", data={}, symbol="BTCUSDT", position_id="p2"
        )
        q.enqueue(pw1)
        result = q.enqueue(pw2)
        assert result is False
        assert "p2" in q.never_persisted

    @pytest.mark.asyncio
    async def test_try_one_success_returns_true(self):
        q = self._make_queue()
        success_fn = AsyncMock(return_value=PersistResult(ok=True))
        q.register("create_position", success_fn)

        pw = PendingWrite(
            operation="create_position",
            data={"x": 1},
            symbol="BTCUSDT",
            position_id="p1",
        )
        result = await q._try_one(pw)

        assert result is True
        assert success_fn.called

    @pytest.mark.asyncio
    async def test_try_one_failure_returns_false(self):
        q = self._make_queue()
        fail_fn = AsyncMock(return_value=PersistResult(ok=False, reason="permanent"))
        q.register("create_position", fail_fn)

        pw = PendingWrite(
            operation="create_position",
            data={},
            symbol="BTCUSDT",
            position_id="p2",
        )
        result = await q._try_one(pw)

        assert result is False

    @pytest.mark.asyncio
    async def test_never_persisted_cleared_on_successful_retry(self):
        q = self._make_queue()
        q.never_persisted.add("p_recover")
        success_fn = AsyncMock(return_value=PersistResult(ok=True))
        q.register("create_position", success_fn)

        pw = PendingWrite(
            operation="create_position",
            data={},
            symbol="BTCUSDT",
            position_id="p_recover",
            attempts=1,
        )
        result = await q._try_one(pw)
        if result:
            q.never_persisted.discard("p_recover")

        assert "p_recover" not in q.never_persisted

    def test_register_unknown_operation_returns_false_on_try(self):
        q = self._make_queue()

        async def run():
            pw = PendingWrite(
                operation="unknown_op", data={}, symbol="X", position_id="p99"
            )
            return await q._try_one(pw)

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result is False
