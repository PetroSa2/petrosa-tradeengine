import asyncio
from unittest.mock import MagicMock, patch

import pytest

from contracts.signal import Signal, StrategyMode
from tradeengine.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_dispatcher_uses_order_params_overrides():
    """
    Verifies that Dispatcher._signal_to_order correctly applies overrides from order_params.
    """
    with patch("tradeengine.dispatcher.Settings"):
        dispatcher = Dispatcher()
        dispatcher.logger = MagicMock()
        dispatcher.position_manager = MagicMock()
        dispatcher.position_manager.total_portfolio_value = 10000.0

        # Base signal
        signal = Signal(
            strategy_id="test_strat",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.5,
            price=50000.0,
            quantity=0.01,
            current_price=50000.0,
            source="unknown",
            strategy="test_strat",
            strategy_mode=StrategyMode.LLM_REASONING,
            stop_loss_pct=0.01,
            take_profit_pct=0.02,
            position_size_pct=0.05,
        )

        # LLM Overrides
        order_params = {
            "position_size_pct": 0.2,  # Override 5% -> 20%
            "stop_loss_pct": 0.05,  # Override 1% -> 5%
            "take_profit_pct": 0.1,  # Override 2% -> 10%
            "side": "sell",  # Override buy -> sell (extreme case)
        }

        with patch("tradeengine.api.binance_exchange") as mock_exchange:
            mock_exchange.calculate_min_order_amount.return_value = 0.001

            order = dispatcher._signal_to_order(signal, order_params)

            # 20% of 10000 = 2000. At price 50000, amount = 0.04
            assert order.amount == 0.04
            assert float(order.stop_loss_pct) == 0.05
            assert float(order.take_profit_pct) == 0.1
            assert order.side == "sell"
            assert order.position_size_pct == 0.2
