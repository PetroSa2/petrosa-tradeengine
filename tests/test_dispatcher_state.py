from unittest.mock import MagicMock, patch

import pytest

from shared.config import Settings
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_managers():
    position_manager = MagicMock()
    order_manager = MagicMock()
    exchange = MagicMock()
    return position_manager, order_manager, exchange

@pytest.fixture
def dispatcher(mock_managers):
    pm, om, ex = mock_managers
    d = Dispatcher(exchange=ex)
    d.position_manager = pm
    d.order_manager = om
    return d

def test_get_cio_state_handles_missing_settings(dispatcher, mock_managers):
    pm, om, _ = mock_managers

    # Setup mocks
    pm.get_cio_portfolio_summary.return_value = {"exposed": True}
    pm.get_daily_pnl.return_value = -100.0
    pm.total_portfolio_value = 10000.0
    om.get_active_orders.return_value = [
        {"symbol": "BTCUSDT", "id": "1"},
        {"symbol": "ETHUSDT", "id": "2"}
    ]

    # Use a settings object that definitely doesn't have the new attributes
    mock_settings = Settings()
    # Deliberately remove them if they exist (unlikely but safe)
    if hasattr(mock_settings, "max_algo_orders"):
        delattr(mock_settings, "max_algo_orders")

    with patch("shared.config.settings", mock_settings):
        state = dispatcher.get_cio_state("BTCUSDT")

        assert "portfolio" in state
        assert state["portfolio"] == {"exposed": True}
        assert state["risk_limits"]["max_orders_global"] == 10  # Fallback value
        assert state["risk_limits"]["max_orders_per_symbol"] == 2  # Fallback value
        assert state["risk_limits"]["max_position_size_usd"] == 1000.0  # Fallback value

        # Check drawdown calculation (should be magnitude)
        # loss is 100, portfolio is 10000 => 0.01
        assert state["env_stats"]["global_drawdown_pct"] == 0.01
        assert state["env_stats"]["open_orders_global"] == 2
        assert state["env_stats"]["open_orders_symbol"] == 1

def test_get_cio_state_positive_pnl(dispatcher, mock_managers):
    pm, om, _ = mock_managers
    pm.get_daily_pnl.return_value = 500.0 # Profit
    pm.total_portfolio_value = 10000.0
    om.get_active_orders.return_value = []
    pm.get_cio_portfolio_summary.return_value = {}

    state = dispatcher.get_cio_state("BTCUSDT")

    # Drawdown should be 0 for profit
    assert state["env_stats"]["global_drawdown_pct"] == 0.0
