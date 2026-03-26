from unittest.mock import MagicMock

import pytest

from shared.constants import UTC
from tradeengine.position_manager import PositionManager


@pytest.fixture
def manager():
    m = PositionManager()
    m.total_portfolio_value = 10000.0
    return m


def test_get_cio_portfolio_summary_basic(manager):
    # Mock positions
    manager.positions = {
        ("BTCUSDT", "LONG"): {
            "quantity": 0.1,
            "avg_price": 40000.0,
            "symbol": "BTCUSDT",
        },
        ("ETHUSDT", "SHORT"): {
            "quantity": -1.0,
            "avg_price": 2000.0,
            "symbol": "ETHUSDT",
        },
    }

    summary = manager.get_cio_portfolio_summary("BTCUSDT")

    # Total exposure = abs(0.1 * 40000) + abs(-1.0 * 2000) = 4000 + 2000 = 6000
    # Gross exposure = 6000 / 10000 = 0.6
    # Same asset pct (BTCUSDT) = 4000 / 10000 = 0.4

    assert summary["gross_exposure"] == 0.6
    assert summary["same_asset_pct"] == 0.4
    assert summary["open_positions_count"] == 2


def test_get_cio_portfolio_summary_zero_portfolio(manager):
    manager.total_portfolio_value = 0.0
    summary = manager.get_cio_portfolio_summary("BTCUSDT")
    assert summary["open_positions_count"] == 0
    assert summary["net_directional_exposure"] == 0.0


def test_get_cio_portfolio_summary_empty(manager):
    manager.positions = {}
    summary = manager.get_cio_portfolio_summary("BTCUSDT")
    assert summary["open_positions_count"] == 0
    assert summary["gross_exposure"] == 0.0
    assert summary["same_asset_pct"] == 0.0
