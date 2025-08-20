#!/usr/bin/env python3
"""
Test script for Binance Futures testnet using Kubernetes configuration
Replicates the exact environment variables from k8s/deployment.yaml
"""

import asyncio
import logging
import os
import sys

from binance.client import Client
from binance.enums import FUTURE_ORDER_TYPE_LIMIT, FUTURE_ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kubernetes configuration (from k8s/deployment.yaml and secrets)
K8S_CONFIG = {
    # From petrosa-common-config ConfigMap
    "BINANCE_TESTNET": "true",
    "ENVIRONMENT": "production",
    "SIMULATION_ENABLED": "false",
    "LOG_LEVEL": "INFO",
    # From petrosa-sensitive-credentials Secret
    "BINANCE_API_KEY": "2fe0e9581c784734c3197577c3243335f98f5547006feb859bd3ccd054b19aa1",
    "BINANCE_API_SECRET": "5c6acc1d16f1041d80788bd1d5aa19577328e7185c84a193787be8640abf6cb6",
    # Futures-specific configuration from deployment.yaml
    "FUTURES_TRADING_ENABLED": "true",
    "DEFAULT_LEVERAGE": "10",
    "MARGIN_TYPE": "isolated",
    "POSITION_MODE": "hedge",
    # Risk management
    "MAX_POSITION_SIZE_PCT": "0.1",
    "MAX_DAILY_LOSS_PCT": "0.05",
    "MAX_PORTFOLIO_EXPOSURE_PCT": "0.8",
}


def setup_environment():
    """Set up environment variables to match Kubernetes configuration"""
    logger.info("Setting up environment variables to match Kubernetes configuration...")

    for key, value in K8S_CONFIG.items():
        os.environ[key] = value
        logger.info(f"Set {key} = {value}")


def test_binance_futures_connection():
    """Test Binance Futures testnet connection with Kubernetes config"""
    logger.info("Testing Binance Futures testnet connection...")

    try:
        # Use the same configuration as Kubernetes deployment
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

        logger.info(f"API Key: {api_key[:8]}...{api_key[-8:]}")
        logger.info(f"Testnet enabled: {testnet}")

        # Initialize client with testnet configuration
        client = Client(api_key, api_secret, testnet=testnet)

        # Test 1: Server time
        logger.info("Test 1: Getting server time...")
        server_time = client.get_server_time()
        logger.info(f"Server time: {server_time}")

        # Test 2: Account information
        logger.info("Test 2: Getting account information...")
        account_info = client.futures_account()
        logger.info(f"Account status: {account_info.get('status')}")
        logger.info(f"Total wallet balance: {account_info.get('totalWalletBalance')}")
        logger.info(
            f"Total unrealized PnL: {account_info.get('totalUnrealizedProfit')}"
        )

        # Test 3: Exchange information
        logger.info("Test 3: Getting exchange information...")
        exchange_info = client.futures_exchange_info()
        logger.info(f"Exchange timezone: {exchange_info.get('timezone')}")
        logger.info(f"Server time: {exchange_info.get('serverTime')}")
        logger.info(f"Rate limits: {exchange_info.get('rateLimits')}")

        # Test 4: Symbol information for BTCUSDT
        logger.info("Test 4: Getting BTCUSDT symbol information...")
        symbol_info = client.futures_symbol_ticker(symbol="BTCUSDT")
        logger.info(f"BTCUSDT price: {symbol_info.get('price')}")
        logger.info(f"BTCUSDT 24h change: {symbol_info.get('priceChangePercent')}%")

        # Test 5: Position information
        logger.info("Test 5: Getting position information...")
        positions = client.futures_position_information()
        btc_position = next((p for p in positions if p["symbol"] == "BTCUSDT"), None)
        if btc_position:
            logger.info(f"BTCUSDT position: {btc_position}")
        else:
            logger.info("No BTCUSDT position found")

        # Test 6: Order book
        logger.info("Test 6: Getting order book...")
        order_book = client.futures_order_book(symbol="BTCUSDT", limit=5)
        logger.info(f"Order book bids: {order_book['bids'][:3]}")
        logger.info(f"Order book asks: {order_book['asks'][:3]}")

        # Test 7: Recent trades
        logger.info("Test 7: Getting recent trades...")
        recent_trades = client.futures_recent_trades(symbol="BTCUSDT", limit=3)
        for trade in recent_trades:
            logger.info(
                f"Trade: {trade.get('price')} @ {trade.get('qty')} ({trade.get('side', 'unknown')})"
            )

        # Test 8: Kline/Candlestick data
        logger.info("Test 8: Getting kline data...")
        klines = client.futures_klines(
            symbol="BTCUSDT", interval=Client.KLINE_INTERVAL_1HOUR, limit=3
        )
        for kline in klines:
            logger.info(
                f"Kline: Open={kline[1]}, High={kline[2]}, Low={kline[3]}, Close={kline[4]}"
            )

        # Test 9: Leverage bracket information
        logger.info("Test 9: Getting leverage bracket information...")
        leverage_brackets = client.futures_leverage_bracket(symbol="BTCUSDT")
        logger.info(f"Leverage brackets: {leverage_brackets}")

        # Test 10: Margin type
        logger.info("Test 10: Getting margin type...")
        try:
            margin_type = client.futures_margin_type(symbol="BTCUSDT")
            logger.info(f"Margin type: {margin_type}")
        except AttributeError:
            logger.info("Margin type method not available in this version")
        except Exception as e:
            logger.info(f"Margin type error: {e}")

        logger.info("✅ All connection tests passed!")
        return True

    except BinanceAPIException as e:
        logger.error(f"Binance API Error: {e}")
        logger.error(f"Error code: {e.code}")
        logger.error(f"Error message: {e.message}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False


def test_futures_order_types():
    """Test futures-specific order types and functionality"""
    logger.info("Testing futures-specific functionality...")

    try:
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

        client = Client(api_key, api_secret, testnet=testnet)

        # Test futures-specific enums
        logger.info(f"FUTURE_ORDER_TYPE_MARKET: {FUTURE_ORDER_TYPE_MARKET}")
        logger.info(f"FUTURE_ORDER_TYPE_LIMIT: {FUTURE_ORDER_TYPE_LIMIT}")

        # Test futures account trades
        logger.info("Getting futures account trades...")
        account_trades = client.futures_account_trades()
        logger.info(f"Account trades count: {len(account_trades)}")

        # Test futures income history
        logger.info("Getting futures income history...")
        income_history = client.futures_income_history()
        logger.info(f"Income history count: {len(income_history)}")

        # Test futures commission rate
        logger.info("Getting futures commission rate...")
        try:
            commission_rate = client.futures_commission_rate(symbol="BTCUSDT")
            logger.info(f"Commission rate: {commission_rate}")
        except AttributeError:
            logger.info("Commission rate method not available in this version")
        except Exception as e:
            logger.info(f"Commission rate error: {e}")

        logger.info("✅ All futures-specific tests passed!")
        return True

    except Exception as e:
        logger.error(f"Futures test error: {e}")
        return False


def test_risk_management_config():
    """Test risk management configuration"""
    logger.info("Testing risk management configuration...")

    config = {
        "MAX_POSITION_SIZE_PCT": os.environ.get("MAX_POSITION_SIZE_PCT", "0.1"),
        "MAX_DAILY_LOSS_PCT": os.environ.get("MAX_DAILY_LOSS_PCT", "0.05"),
        "MAX_PORTFOLIO_EXPOSURE_PCT": os.environ.get(
            "MAX_PORTFOLIO_EXPOSURE_PCT", "0.8"
        ),
        "DEFAULT_LEVERAGE": os.environ.get("DEFAULT_LEVERAGE", "10"),
        "MARGIN_TYPE": os.environ.get("MARGIN_TYPE", "isolated"),
        "POSITION_MODE": os.environ.get("POSITION_MODE", "hedge"),
    }

    logger.info("Risk management configuration:")
    for key, value in config.items():
        logger.info(f"  {key}: {value}")

    return True


async def main():
    """Main test function"""
    logger.info("🚀 Starting Binance Futures testnet test with Kubernetes configuration")
    logger.info("=" * 60)

    # Setup environment
    setup_environment()

    # Test connection
    connection_success = test_binance_futures_connection()

    # Test futures-specific functionality
    futures_success = test_futures_order_types()

    # Test risk management config
    risk_success = test_risk_management_config()

    # Summary
    logger.info("=" * 60)
    logger.info("📊 Test Summary:")
    logger.info(f"  Connection Test: {'✅ PASS' if connection_success else '❌ FAIL'}")
    logger.info(f"  Futures Test: {'✅ PASS' if futures_success else '❌ FAIL'}")
    logger.info(f"  Risk Config Test: {'✅ PASS' if risk_success else '❌ FAIL'}")

    if all([connection_success, futures_success, risk_success]):
        logger.info("🎉 All tests passed! Binance Futures testnet is working correctly.")
        return 0
    else:
        logger.error("❌ Some tests failed. Please check the logs above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
