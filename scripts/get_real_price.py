#!/usr/bin/env python3
"""
Get Real BTC Price for Testing
"""

import asyncio
import logging

from tradeengine.exchange.binance import BinanceFuturesExchange

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def get_real_price():
    """Get real BTC price"""
    try:
        exchange = BinanceFuturesExchange()
        ticker = exchange.client.futures_symbol_ticker(symbol="BTCUSDT")
        price = float(ticker["price"])
        logger.info(f"Real BTCUSDT Price: ${price:,.2f}")
        return price
    except Exception as e:
        logger.error(f"Error: {e}")
        return None


if __name__ == "__main__":
    asyncio.run(get_real_price())
