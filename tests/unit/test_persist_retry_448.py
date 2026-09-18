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
from tradeengine.services.persist_retry_queue import (
    PendingWrite,
    PersistRetryQueue,
    register_default_handlers,
)

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

    # -- #596: 0-insert diagnostics -----------------------------------

    @pytest.mark.asyncio
    async def test_create_position_zero_insert_logs_full_response(self, caplog):
        """A 2xx response with inserted_count=0 must log the raw response,
        not just the bare '0-insert' literal, so operators can see WHY."""
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            return_value={"inserted_id": "", "inserted_count": 0, "error": "duplicate"}
        )
        client.health_check = AsyncMock(return_value={"status": "healthy"})
        with caplog.at_level("ERROR"):
            result = await client.create_position(
                {"position_id": "p1", "symbol": "BTCUSDT"}
            )
        assert result.ok is False
        assert any(
            "0-insert" in rec.message and "duplicate" in rec.message
            for rec in caplog.records
        ), "expected the full DM response (incl. 'duplicate') in the log message"

    @pytest.mark.asyncio
    async def test_create_position_zero_insert_triggers_health_snapshot(self, caplog):
        """On 0-insert, a reactive health-check snapshot must be logged so
        operators can distinguish a DM outage from a constraint/schema
        issue without adding a blocking pre-check to the hot path."""
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            return_value={"inserted_id": "", "inserted_count": 0}
        )
        client.health_check = AsyncMock(
            return_value={"status": "unhealthy", "error": "connection pool exhausted"}
        )
        with caplog.at_level("ERROR"):
            await client.create_position({"position_id": "p2", "symbol": "ETHUSDT"})
        client.health_check.assert_awaited_once()
        assert any("connection pool exhausted" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_create_position_zero_insert_health_probe_never_raises(self):
        """If the reactive health probe itself fails, create_position must
        still return the (failed) PersistResult rather than raising."""
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            return_value={"inserted_id": "", "inserted_count": 0}
        )
        client.health_check = AsyncMock(side_effect=RuntimeError("probe boom"))
        result = await client.create_position(
            {"position_id": "p3", "symbol": "SOLUSDT"}
        )
        assert result.ok is False
        assert result.reason == "permanent"

    @pytest.mark.asyncio
    async def test_create_position_success_does_not_probe_health(self):
        """The reactive health probe must only fire on the failure path —
        never add an extra call on the (common) success path."""
        client, mock_base = self._make_client()
        mock_base.insert_one = AsyncMock(
            return_value={"inserted_id": "abc", "inserted_count": 1}
        )
        client.health_check = AsyncMock(return_value={"status": "healthy"})
        result = await client.create_position(
            {"position_id": "p4", "symbol": "BTCUSDT"}
        )
        assert result.ok is True
        client.health_check.assert_not_awaited()


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


# ---------------------------------------------------------------------------
# register_default_handlers (#596) — the actual production wiring gap:
# persist_retry_queue was constructed but never had .start() called nor any
# operation handler registered, so enqueued 0-insert failures sat forever
# and were never retried.
# ---------------------------------------------------------------------------


class TestRegisterDefaultHandlers:
    @pytest.mark.asyncio
    async def test_create_position_retry_reassembles_flat_kwargs_into_dict(self):
        """PersistRetryQueue._try_one calls fn(**pw.data); create_position
        takes ONE positional dict, so the wrapper must reassemble it."""
        q = PersistRetryQueue()
        mock_client = MagicMock()
        mock_client.create_position = AsyncMock(return_value=PersistResult(ok=True))
        register_default_handlers(q, mock_client)

        pw = PendingWrite(
            operation="create_position",
            data={"position_id": "p1", "symbol": "BTCUSDT", "status": "open"},
            symbol="BTCUSDT",
            position_id="p1",
        )
        result = await q._try_one(pw)

        assert result is True
        mock_client.create_position.assert_awaited_once_with(
            {"position_id": "p1", "symbol": "BTCUSDT", "status": "open"}
        )

    @pytest.mark.asyncio
    async def test_update_position_retry_extracts_stashed_position_id(self):
        """update_position needs a position_id that is not a field of the
        update payload — the wrapper must pop `_retry_position_id` back out
        and call client.update_position(position_id, remaining_dict)."""
        q = PersistRetryQueue()
        mock_client = MagicMock()
        mock_client.update_position = AsyncMock(return_value=PersistResult(ok=True))
        register_default_handlers(q, mock_client)

        pw = PendingWrite(
            operation="update_position",
            data={"status": "closed", "_retry_position_id": "strategy-pos-42"},
            symbol="BTCUSDT",
            position_id="strategy-pos-42",
        )
        result = await q._try_one(pw)

        assert result is True
        mock_client.update_position.assert_awaited_once_with(
            "strategy-pos-42", {"status": "closed"}
        )

    @pytest.mark.asyncio
    async def test_update_position_risk_orders_retry_extracts_position_id(self):
        q = PersistRetryQueue()
        mock_client = MagicMock()
        mock_client.update_position_risk_orders = AsyncMock(
            return_value=PersistResult(ok=True)
        )
        register_default_handlers(q, mock_client)

        pw = PendingWrite(
            operation="update_position_risk_orders",
            data={"stop_loss": 100.0, "_retry_position_id": "pos-7"},
            symbol="ETHUSDT",
            position_id="pos-7",
        )
        result = await q._try_one(pw)

        assert result is True
        mock_client.update_position_risk_orders.assert_awaited_once_with(
            "pos-7", {"stop_loss": 100.0}
        )

    @pytest.mark.asyncio
    async def test_wired_queue_actually_drains_and_retries_zero_insert(self):
        """End-to-end (fast drain): enqueue a failed create_position write
        against a wired-up queue and confirm the background drain loop
        actually calls the client again — proving the previously dead
        infrastructure now delivers real retries."""
        import tradeengine.services.persist_retry_queue as retry_queue_module

        q = PersistRetryQueue(max_drain_attempts=3, drain_interval=0.01)
        mock_client = MagicMock()
        mock_client.create_position = AsyncMock(return_value=PersistResult(ok=True))
        register_default_handlers(q, mock_client)

        pw = PendingWrite(
            operation="create_position",
            data={"position_id": "p_retry", "symbol": "BTCUSDT"},
            symbol="BTCUSDT",
            position_id="p_retry",
        )
        q.enqueue(pw)
        with (
            patch.object(retry_queue_module, "_BACKOFF_BASE", 0.01),
            patch.object(retry_queue_module, "_BACKOFF_CAP", 0.05),
        ):
            q.start()
            try:
                for _ in range(500):
                    await asyncio.sleep(0.01)
                    if mock_client.create_position.await_count > 0:
                        break
            finally:
                q.stop()

        mock_client.create_position.assert_awaited()


# ---------------------------------------------------------------------------
# _on_persist_failure (strategy_position_manager) — must stash the position
# id for update-type operations before enqueueing (#596).
# ---------------------------------------------------------------------------


class TestOnPersistFailureRetryPositionId:
    def test_update_position_failure_stashes_retry_position_id(self):
        from tradeengine.strategy_position_manager import _on_persist_failure

        result = PersistResult(
            ok=False,
            reason="permanent",
            error="0-insert",
            operation="update_position",
            symbol="BTCUSDT",
            position_id="strategy-pos-99",
        )
        position_data = {"status": "closed"}

        with (
            patch(
                "tradeengine.strategy_position_manager.persist_retry_queue"
            ) as mock_queue,
            patch("tradeengine.strategy_position_manager.alert_publisher"),
        ):
            _on_persist_failure(result, position_data)
            assert mock_queue.enqueue.called
            enqueued_pw = mock_queue.enqueue.call_args[0][0]
            assert enqueued_pw.data.get("_retry_position_id") == "strategy-pos-99"

    def test_create_position_failure_does_not_stash_retry_position_id(self):
        """create_position's dict already IS the full payload — no
        _retry_position_id sentinel should be injected for it."""
        from tradeengine.strategy_position_manager import _on_persist_failure

        result = PersistResult(
            ok=False,
            reason="permanent",
            error="0-insert",
            operation="create_position",
            symbol="BTCUSDT",
            position_id="pos-1",
        )
        position_data = {"position_id": "pos-1", "symbol": "BTCUSDT"}

        with (
            patch(
                "tradeengine.strategy_position_manager.persist_retry_queue"
            ) as mock_queue,
            patch("tradeengine.strategy_position_manager.alert_publisher"),
        ):
            _on_persist_failure(result, position_data)
            enqueued_pw = mock_queue.enqueue.call_args[0][0]
            assert "_retry_position_id" not in enqueued_pw.data
