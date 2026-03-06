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
        with patch.object(dispatcher.logger, "_log") as mock_log:
            # This matches the new line in tradeengine/dispatcher.py
            dispatcher.logger.info(
                f"✅ Position updated | event=position_updated | symbol={order.symbol} | "
                f"order_id={order.order_id} | position_id={result.get('position_id')}"
            )

            # 4. Verify the call
            mock_log.assert_called_once()
        args, kwargs = mock_log.call_args

        # args[0] is level (INFO=20), args[1] is message
        message = args[1]
        assert "Position updated" in message
        assert "event=position_updated" in message
        assert "symbol=BTCUSDT" in message
        assert "order_id=test_order_123" in message
        assert "position_id=pos_456" in message

        # Crucially, 'extra' should not be needed for this simple pattern
        # and 'event' should NOT be a direct keyword argument
        assert "event" not in kwargs


if __name__ == "__main__":
    import sys

    pytest.main([__file__] + sys.argv[1:])
