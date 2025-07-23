#!/usr/bin/env python3
"""
Test script to open a real BTCUSDT position on Binance Futures testnet
This will verify that our trading engine is actually hitting Binance and executing trades
"""

import asyncio
import logging
import os
import sys
import time

from binance.client import Client
from binance.enums import FUTURE_ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL
from binance.exceptions import BinanceAPIException

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Kubernetes configuration (same as before)
K8S_CONFIG = {
    "BINANCE_TESTNET": "true",
    "ENVIRONMENT": "production",
    "SIMULATION_ENABLED": "false",
    "LOG_LEVEL": "INFO",
    "BINANCE_API_KEY": "2fe0e9581c784734c3197577c3243335f98f5547006feb859bd3ccd054b19aa1",
    "BINANCE_API_SECRET": "5c6acc1d16f1041d80788bd1d5aa19577328e7185c84a193787be8640abf6cb6",
    "FUTURES_TRADING_ENABLED": "true",
    "DEFAULT_LEVERAGE": "10",
    "MARGIN_TYPE": "isolated",
    "POSITION_MODE": "hedge",
}


def setup_environment():
    """Set up environment variables to match Kubernetes configuration"""
    logger.info("Setting up environment variables to match Kubernetes configuration...")

    for key, value in K8S_CONFIG.items():
        os.environ[key] = value
        logger.info(f"Set {key} = {value}")


def get_symbol_info(client, symbol="BTCUSDT"):
    """Get symbol information including minimum order size"""
    try:
        exchange_info = client.futures_exchange_info()
        symbol_info = next(
            (s for s in exchange_info["symbols"] if s["symbol"] == symbol), None
        )

        if symbol_info:
            # Find the LOT_SIZE filter for minimum order quantity
            lot_size_filter = next(
                (f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"),
                None,
            )
            min_qty = float(lot_size_filter["minQty"]) if lot_size_filter else 0.001

            # Find the MIN_NOTIONAL filter for minimum order value
            min_notional_filter = next(
                (
                    f
                    for f in symbol_info["filters"]
                    if f["filterType"] == "MIN_NOTIONAL"
                ),
                None,
            )
            min_notional = (
                float(min_notional_filter["notional"]) if min_notional_filter else 5.0
            )

            logger.info(f"Symbol: {symbol}")
            logger.info(f"Minimum quantity: {min_qty}")
            logger.info(f"Minimum notional: {min_notional}")
            logger.info(f"Price precision: {symbol_info.get('pricePrecision')}")
            logger.info(f"Quantity precision: {symbol_info.get('quantityPrecision')}")

            return {
                "min_qty": min_qty,
                "min_notional": min_notional,
                "price_precision": symbol_info.get("pricePrecision"),
                "quantity_precision": symbol_info.get("quantityPrecision"),
            }
        else:
            logger.error(f"Symbol {symbol} not found")
            return None

    except Exception as e:
        logger.error(f"Error getting symbol info: {e}")
        return None


def get_current_price(client, symbol="BTCUSDT"):
    """Get current price for the symbol"""
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        price = float(ticker["price"])
        logger.info(f"Current {symbol} price: ${price:,.2f}")
        return price
    except Exception as e:
        logger.error(f"Error getting current price: {e}")
        return None


def calculate_min_order_size(client, symbol="BTCUSDT"):
    """Calculate the minimum order size that meets all requirements"""
    symbol_info = get_symbol_info(client, symbol)
    current_price = get_current_price(client, symbol)

    if not symbol_info or not current_price:
        return None

    min_qty = symbol_info["min_qty"]
    min_notional = symbol_info["min_notional"]

    # Calculate minimum quantity based on notional value
    min_qty_by_notional = min_notional / current_price

    # Use the larger of the two minimums
    final_min_qty = max(min_qty, min_qty_by_notional)

    # Round to the appropriate precision
    quantity_precision = symbol_info["quantity_precision"]
    final_min_qty = round(final_min_qty, quantity_precision)

    notional_value = final_min_qty * current_price

    logger.info("Calculated minimum order:")
    logger.info(f"  Quantity: {final_min_qty}")
    logger.info(f"  Notional value: ${notional_value:.2f}")
    logger.info(f"  Price: ${current_price:,.2f}")

    return {
        "quantity": final_min_qty,
        "price": current_price,
        "notional": notional_value,
    }


def open_long_position(client, symbol="BTCUSDT"):
    """Open a long position with minimum size"""
    logger.info(f"🟢 Opening LONG position for {symbol}...")

    order_size = calculate_min_order_size(client, symbol)
    if not order_size:
        logger.error("Could not calculate order size")
        return None

    try:
        # Place market order to buy
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=order_size["quantity"],
        )

        logger.info("✅ LONG position opened successfully!")
        logger.info(f"  Order ID: {order['orderId']}")
        logger.info(f"  Symbol: {order['symbol']}")
        logger.info(f"  Side: {order['side']}")
        logger.info(f"  Quantity: {order['origQty']}")
        logger.info(f"  Status: {order['status']}")
        if "time" in order:
            logger.info(f"  Time: {order['time']}")

        return order

    except BinanceAPIException as e:
        logger.error(f"Binance API Error: {e}")
        logger.error(f"Error code: {e.code}")
        logger.error(f"Error message: {e.message}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None


def open_short_position(client, symbol="BTCUSDT"):
    """Open a short position with minimum size"""
    logger.info(f"🔴 Opening SHORT position for {symbol}...")

    order_size = calculate_min_order_size(client, symbol)
    if not order_size:
        logger.error("Could not calculate order size")
        return None

    try:
        # Place market order to sell
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=order_size["quantity"],
        )

        logger.info("✅ SHORT position opened successfully!")
        logger.info(f"  Order ID: {order['orderId']}")
        logger.info(f"  Symbol: {order['symbol']}")
        logger.info(f"  Side: {order['side']}")
        logger.info(f"  Quantity: {order['origQty']}")
        logger.info(f"  Status: {order['status']}")
        if "time" in order:
            logger.info(f"  Time: {order['time']}")

        return order

    except BinanceAPIException as e:
        logger.error(f"Binance API Error: {e}")
        logger.error(f"Error code: {e.code}")
        logger.error(f"Error message: {e.message}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None


def check_position(client, symbol="BTCUSDT"):
    """Check current position for the symbol"""
    try:
        positions = client.futures_position_information(symbol=symbol)
        position = positions[0] if positions else None

        if position:
            logger.info(f"📊 Current {symbol} position:")
            logger.info(f"  Position Amount: {position['positionAmt']}")
            logger.info(f"  Entry Price: {position['entryPrice']}")
            logger.info(f"  Mark Price: {position['markPrice']}")
            logger.info(f"  Unrealized PnL: {position['unRealizedProfit']}")
            logger.info(f"  Liquidation Price: {position['liquidationPrice']}")
            logger.info(f"  Leverage: {position['leverage']}")
            logger.info(f"  Margin Type: {position['marginType']}")
            logger.info(f"  Position Side: {position['positionSide']}")

            # Check if we have an active position
            position_amt = float(position["positionAmt"])
            if position_amt > 0:
                logger.info(f"🟢 LONG position active: {position_amt}")
            elif position_amt < 0:
                logger.info(f"🔴 SHORT position active: {position_amt}")
            else:
                logger.info("⚪ No position active")

            return position
        else:
            logger.info(f"No position found for {symbol}")
            return None

    except Exception as e:
        logger.error(f"Error checking position: {e}")
        return None


def close_position(client, symbol="BTCUSDT"):
    """Close any open position"""
    logger.info(f"🔄 Closing position for {symbol}...")

    try:
        positions = client.futures_position_information(symbol=symbol)
        position = positions[0] if positions else None

        if not position:
            logger.info("No position to close")
            return None

        position_amt = float(position["positionAmt"])

        if position_amt == 0:
            logger.info("No position to close")
            return None

        # Determine side to close position
        if position_amt > 0:  # Long position
            side = SIDE_SELL
            logger.info(f"Closing LONG position of {position_amt}")
        else:  # Short position
            side = SIDE_BUY
            logger.info(f"Closing SHORT position of {abs(position_amt)}")

        # Close position with market order
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=abs(position_amt),
        )

        logger.info("✅ Position closed successfully!")
        logger.info(f"  Order ID: {order['orderId']}")
        logger.info(f"  Status: {order['status']}")

        return order

    except BinanceAPIException as e:
        logger.error(f"Binance API Error: {e}")
        logger.error(f"Error code: {e.code}")
        logger.error(f"Error message: {e.message}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None


async def main():
    """Main test function"""
    logger.info("🚀 Starting BTCUSDT position test on Binance Futures testnet")
    logger.info("=" * 70)

    # Setup environment
    setup_environment()

    # Initialize client
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

    client = Client(api_key, api_secret, testnet=testnet)

    # Check initial position
    logger.info("📊 Checking initial position...")
    # initial_position = check_position(client)

    # Calculate minimum order size
    logger.info("📏 Calculating minimum order size...")
    order_size = calculate_min_order_size(client)

    if not order_size:
        logger.error("❌ Could not calculate order size. Exiting.")
        return 1

    # Ask user what to do
    print("\n" + "=" * 50)
    print("🤔 What would you like to do?")
    print("1. Open LONG position (minimum size)")
    print("2. Open SHORT position (minimum size)")
    print("3. Close any existing position")
    print("4. Check current position only")
    print("5. Exit")
    print("=" * 50)

    choice = input("Enter your choice (1-5): ").strip()

    if choice == "1":
        # Open long position
        order = open_long_position(client)
        if order:
            logger.info("⏳ Waiting 5 seconds to check position...")
            time.sleep(5)
            check_position(client)

    elif choice == "2":
        # Open short position
        order = open_short_position(client)
        if order:
            logger.info("⏳ Waiting 5 seconds to check position...")
            time.sleep(5)
            check_position(client)

    elif choice == "3":
        # Close position
        close_position(client)
        logger.info("⏳ Waiting 5 seconds to check position...")
        time.sleep(5)
        check_position(client)

    elif choice == "4":
        # Check position only
        check_position(client)

    elif choice == "5":
        logger.info("👋 Exiting...")
        return 0

    else:
        logger.error("❌ Invalid choice")
        return 1

    logger.info("=" * 70)
    logger.info("✅ Test completed!")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
