"""Tests for the read-only position journal reconciliation operator tool."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "reconcile_positions_journal.py"
SPEC = importlib.util.spec_from_file_location(
    "reconcile_positions_journal", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


def _exchange_client() -> Mock:
    client = Mock(spec=["futures_position_information"])
    client.futures_position_information.return_value = [
        {"symbol": "ADAUSDT", "positionSide": "LONG", "positionAmt": "877"}
    ]
    return client


def _database_client() -> Mock:
    client = Mock()
    client.get_open_positions = AsyncMock(
        return_value=[
            {
                "position_id": "ada-1",
                "symbol": "ADAUSDT",
                "position_side": "LONG",
                "quantity": 10,
            },
            {
                "position_id": "ada-2",
                "symbol": "ADAUSDT",
                "position_side": "LONG",
                "quantity": 20,
            },
            {
                "position_id": "btc-1",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "quantity": 1,
            },
            {
                "position_id": "btc-2",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "quantity": 2,
            },
            {
                "position_id": "btc-3",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "quantity": 3,
            },
        ]
    )
    client.update_position = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_dry_run_classifies_rows_without_updates():
    exchange = _exchange_client()
    database = _database_client()

    report = await SCRIPT.reconcile(
        exchange,
        database,
        apply=False,
        confirm_count=None,
    )

    assert report["phantom"] == 3
    assert report["live-candidate"] == 2
    database.update_position.assert_not_called()
    exchange.futures_position_information.assert_called_once_with()


@pytest.mark.asyncio
async def test_apply_updates_each_phantom_by_position_id():
    exchange = _exchange_client()
    database = _database_client()
    database.update_position.return_value = Mock(ok=True)
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    report = await SCRIPT.reconcile(
        exchange,
        database,
        apply=True,
        confirm_count=3,
        now=now,
    )

    assert report["applied"] == 3
    assert database.update_position.await_count == 3
    for call in database.update_position.await_args_list:
        position_id, update = call.args
        assert position_id in {"btc-1", "btc-2", "btc-3"}
        assert update == {
            "status": "closed",
            "close_reason": "reconciled_no_exchange_position",
            "exit_time": "2026-09-25T12:00:00+00:00",
            "pnl": None,
        }
        assert "$set" not in update


@pytest.mark.asyncio
async def test_confirmation_mismatch_issues_no_updates():
    exchange = _exchange_client()
    database = _database_client()

    with pytest.raises(SCRIPT.ReconciliationError, match="does not match"):
        await SCRIPT.reconcile(
            exchange,
            database,
            apply=True,
            confirm_count=2,
        )

    database.update_position.assert_not_called()


@pytest.mark.asyncio
async def test_exchange_client_is_limited_to_position_information():
    exchange = _exchange_client()
    database = _database_client()

    await SCRIPT.reconcile(exchange, database, apply=False, confirm_count=None)

    exchange.futures_position_information.assert_called_once_with()
    assert exchange.method_calls == [("futures_position_information", (), {})]
