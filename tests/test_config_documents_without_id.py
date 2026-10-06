"""Config reads must work on the documents data-manager really returns: they carry no `_id`.

Data-manager strips `_id` from every query result. The global trading config written on 2026-10-06
(max_position_size_usd=110) was never read back: each getter raised KeyError('_id'), the error was logged
and resolution fell back to the 1000.0 default.
"""

import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient


def _document(max_position_size_usd, **scope):
    """A stored config as the data-manager query returns it: no `_id`."""
    return {
        "parameters": {"max_position_size_usd": max_position_size_usd},
        "version": 1,
        "created_at": "2026-10-06T18:53:00+00:00",
        "updated_at": "2026-10-06T18:53:00+00:00",
        "created_by": "operator",
        "metadata": {},
        **scope,
    }


def _client(stored):
    """A config client whose data-manager answers per collection."""

    async def query(database, collection, filter=None, limit=None, **_):
        assert database == "mongodb"
        return {"data": [dict(item) for item in stored.get(collection, [])]}

    with patch("tradeengine.db.mongodb_client.DataManagerClient") as manager:
        manager.return_value._client.query = AsyncMock(side_effect=query)
        return DataManagerConfigClient()


@pytest.mark.asyncio
async def test_global_config_without_id_returns_the_stored_value():
    client = _client({"trading_configs_global": [_document(110)]})

    config = await client.get_global_config()

    assert config is not None
    assert config.parameters["max_position_size_usd"] == 110
    assert config.id == "global"


@pytest.mark.asyncio
async def test_symbol_symbol_side_and_strategy_configs_without_id():
    client = _client(
        {
            "trading_configs_symbols": [_document(55, symbol="BTCUSDT")],
            "trading_configs_symbol_side": [
                _document(33, symbol="BTCUSDT", side="LONG")
            ],
            "trading_configs_strategy": [_document(77, strategy_id="momentum")],
        }
    )

    symbol = await client.get_symbol_config("BTCUSDT")
    side = await client.get_symbol_side_config("BTCUSDT", "LONG")
    strategy = await client.get_strategy_config("momentum")

    assert (symbol.id, symbol.parameters["max_position_size_usd"]) == ("BTCUSDT", 55)
    assert (side.id, side.parameters["max_position_size_usd"]) == ("BTCUSDT:LONG", 33)
    assert (strategy.id, strategy.parameters["max_position_size_usd"]) == (
        "momentum",
        77,
    )


@pytest.mark.asyncio
async def test_a_stored_id_is_kept_when_the_document_has_one():
    with_underscore = _document(110)
    with_underscore["_id"] = "abc"
    with_id = _document(110, id="def")
    assert (
        await _client({"trading_configs_global": [with_underscore]}).get_global_config()
    ).id == "abc"
    assert (
        await _client({"trading_configs_global": [with_id]}).get_global_config()
    ).id == "def"


@pytest.mark.asyncio
async def test_every_scope_resolves_the_global_value_not_the_default():
    client = _client({"trading_configs_global": [_document(110)]})
    manager = TradingConfigManager(mongodb_client=client)

    assert (await manager.get_config())["max_position_size_usd"] == 110
    assert (await manager.get_config("BTCUSDT"))["max_position_size_usd"] == 110
    assert (await manager.get_config("BTCUSDT", "LONG"))["max_position_size_usd"] == 110


@pytest.mark.asyncio
async def test_narrower_scopes_override_the_global_value():
    client = _client(
        {
            "trading_configs_global": [_document(110)],
            "trading_configs_symbols": [_document(55, symbol="BTCUSDT")],
            "trading_configs_symbol_side": [
                _document(33, symbol="BTCUSDT", side="LONG")
            ],
        }
    )
    manager = TradingConfigManager(mongodb_client=client)

    assert (await manager.get_config("BTCUSDT"))["max_position_size_usd"] == 55
    assert (await manager.get_config("BTCUSDT", "LONG"))["max_position_size_usd"] == 33


@pytest.mark.asyncio
async def test_a_real_failure_is_still_logged_and_returns_none(caplog):
    with patch("tradeengine.db.mongodb_client.DataManagerClient") as manager:
        manager.return_value._client.query = AsyncMock(
            side_effect=RuntimeError("data-manager down")
        )
        client = DataManagerConfigClient()

    with caplog.at_level(logging.ERROR):
        assert await client.get_global_config() is None

    assert "Failed to get global config from Data Manager" in caplog.text
