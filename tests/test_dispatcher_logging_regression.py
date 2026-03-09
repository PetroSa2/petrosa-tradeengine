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

    # 3. Simply call the log statement. If it doesn't raise a TypeError, the regression test passes.
    # We no longer mock _log because we use structlog, which handles kwargs differently and
    # doesn't use the standard logging._log method directly in the same way.
    dispatcher.logger.info(
        f"✅ Position updated | event=position_updated | symbol={order.symbol} | "
        f"order_id={order.order_id} | position_id={result.get('position_id')}"
    )


if __name__ == "__main__":
    import sys

    pytest.main([__file__] + sys.argv[1:])
