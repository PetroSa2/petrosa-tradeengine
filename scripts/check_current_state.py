#!/usr/bin/env python3
"""
Check Current Positions and Orders
"""

import asyncio
import logging

from tradeengine.exchange.binance import BinanceFuturesExchange

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_current_state():
    """Check current positions and orders"""
    try:
        exchange = BinanceFuturesExchange()

        # Check positions
        logger.info("=== CURRENT POSITIONS ===")
        positions = exchange.client.futures_position_information()
        for pos in positions:
            if float(pos["positionAmt"]) != 0:
                logger.info(f"Symbol: {pos['symbol']}")
                logger.info(f"  Position: {pos['positionAmt']}")
                logger.info(f"  Entry Price: {pos['entryPrice']}")
                logger.info(f"  Mark Price: {pos['markPrice']}")
                logger.info(f"  PnL: {pos['unrealizedPnl']}")
                logger.info(f"  Position Side: {pos['positionSide']}")
                logger.info("")

        # Check open orders
        logger.info("=== OPEN ORDERS ===")
        orders = exchange.client.futures_get_open_orders()
        logger.info(f"Total open orders: {len(orders)}")
        for order in orders:
            logger.info(f"Order ID: {order['orderId']}")
            logger.info(f"  Symbol: {order['symbol']}")
            logger.info(f"  Type: {order['type']}")
            logger.info(f"  Side: {order['side']}")
            logger.info(f"  Status: {order['status']}")
            logger.info(f"  Quantity: {order['origQty']}")
            logger.info(f"  Price: {order['price']}")
            logger.info(f"  Stop Price: {order.get('stopPrice', 'N/A')}")
            logger.info(f"  Reduce Only: {order['reduceOnly']}")
            logger.info("")

        # Get current price
        logger.info("=== CURRENT PRICE ===")
        ticker = exchange.client.futures_symbol_ticker(symbol="BTCUSDT")
        price = float(ticker["price"])
        logger.info(f"BTCUSDT Current Price: ${price:,.2f}")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(check_current_state())
