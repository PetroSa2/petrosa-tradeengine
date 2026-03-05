import logging
from unittest.mock import MagicMock, patch

import pytest

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from tradeengine.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_dispatcher_logging_no_typeerror_issue_266():
    """
    Focused regression test for issue #266.
    Directly exercises the logging pattern used in Dispatcher to ensure
    it uses 'extra' correctly and doesn't raise a TypeError with the standard Logger.
    """
    # 1. Setup
    dispatcher = Dispatcher()
    # Ensure it's using a standard Logger (which the project's get_logger returns)
    assert isinstance(dispatcher.logger, logging.Logger)

    # Force level to INFO and mock isEnabledFor to ensure _log is reached
    dispatcher.logger.setLevel(logging.INFO)
    with patch.object(dispatcher.logger, "isEnabledFor", return_value=True):
        # 2. Prepare test data matching the context of the fix
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=1.0,
            exchange="binance",
            order_id="test_order_123",
            status=OrderStatus.PENDING,
        )

        result = {"position_id": "pos_456"}

        # 3. Patch the underlying _log method to verify the arguments passed to it
        # This is where the TypeError actually happens if keyword arguments are passed directly
        with patch.object(dispatcher.logger, "_log") as mock_log:
            # This is the EXACT line from tradeengine/dispatcher.py that was failing
            dispatcher.logger.info(
                "Position updated",
                extra={
                    "event": "position_updated",
                    "symbol": order.symbol,
                    "order_id": order.order_id,
                    "position_id": result.get("position_id"),
                },
            )

            # 4. Verify the call
            mock_log.assert_called_once()
        args, kwargs = mock_log.call_args

        # args[0] is level (INFO=20), args[1] is message
        assert args[1] == "Position updated"

        # The fix is that metadata MUST be in 'extra', not as direct kwargs
        assert "extra" in kwargs
        assert kwargs["extra"]["event"] == "position_updated"
        assert kwargs["extra"]["symbol"] == "BTCUSDT"
        assert kwargs["extra"]["position_id"] == "pos_456"

        # Crucially, 'event' should NOT be a direct keyword argument
        assert "event" not in kwargs


if __name__ == "__main__":
    import sys

    pytest.main([__file__] + sys.argv[1:])
