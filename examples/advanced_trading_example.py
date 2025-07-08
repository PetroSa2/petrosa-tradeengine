#!/usr/bin/env python3
"""
Advanced Trading Example - Petrosa Trading Engine

This example demonstrates the comprehensive trading capabilities including:
- All order types (market, limit, stop, stop-limit, take-profit)
- Stop loss and take profit orders
- Live trading vs simulation
- Account information and order management

Usage:
    python examples/advanced_trading_example.py
"""

import asyncio
import os
import sys
from datetime import datetime

from contracts.signal import Signal
from shared.constants import BINANCE_TESTNET, SIMULATION_ENABLED
from tradeengine.dispatcher import dispatcher

# Add project root to path so we can import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def demonstrate_market_orders():
    """Demonstrate market order execution"""
    print("\n" + "=" * 60)
    print("MARKET ORDERS")
    print("=" * 60)

    # Simple market buy order
    market_buy_signal = Signal(
        strategy_id="market_example",
        symbol="BTCUSDT",
        action="buy",
        price=45000.0,
        confidence=0.8,
        timestamp=datetime.now(),
        meta={
            "order_type": "market",
            "base_amount": 0.001,  # 0.001 BTC
            "simulate": True,
            "description": "Simple market buy order",
        },
    )

    result = await dispatcher.dispatch(market_buy_signal)
    print(f"Market Buy Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Fill Price: {result.get('fill_price')}")
    print(f"Amount: {result.get('amount')}")
    print(f"Fees: {result.get('fees')}")


async def demonstrate_limit_orders():
    """Demonstrate limit order execution"""
    print("\n" + "=" * 60)
    print("LIMIT ORDERS")
    print("=" * 60)

    # Limit buy order below current price
    limit_buy_signal = Signal(
        strategy_id="limit_example",
        symbol="BTCUSDT",
        action="buy",
        price=44000.0,  # Target price below current
        confidence=0.9,
        timestamp=datetime.now(),
        meta={
            "order_type": "limit",
            "base_amount": 0.001,
            "time_in_force": "GTC",
            "simulate": True,
            "description": "Limit buy order below current price",
        },
    )

    result = await dispatcher.dispatch(limit_buy_signal)
    print(f"Limit Buy Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Target Price: {result.get('original_order', {}).get('target_price')}")


async def demonstrate_stop_orders():
    """Demonstrate stop order execution"""
    print("\n" + "=" * 60)
    print("STOP ORDERS")
    print("=" * 60)

    # Stop loss order for a long position
    stop_loss_signal = Signal(
        strategy_id="stop_example",
        symbol="BTCUSDT",
        action="sell",
        price=45000.0,
        confidence=0.95,
        timestamp=datetime.now(),
        meta={
            "order_type": "stop",
            "base_amount": 0.001,
            "stop_loss": 43000.0,  # Stop loss at 43k
            "use_default_stop_loss": False,
            "simulate": True,
            "description": "Stop loss order for long position",
        },
    )

    result = await dispatcher.dispatch(stop_loss_signal)
    print(f"Stop Loss Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Stop Price: {result.get('original_order', {}).get('stop_loss')}")


async def demonstrate_stop_limit_orders():
    """Demonstrate stop-limit order execution"""
    print("\n" + "=" * 60)
    print("STOP-LIMIT ORDERS")
    print("=" * 60)

    # Stop-limit order with both stop and limit prices
    stop_limit_signal = Signal(
        strategy_id="stop_limit_example",
        symbol="BTCUSDT",
        action="sell",
        price=45000.0,
        confidence=0.85,
        timestamp=datetime.now(),
        meta={
            "order_type": "stop_limit",
            "base_amount": 0.001,
            "stop_loss": 43000.0,  # Stop trigger price
            "target_price": 42900.0,  # Limit execution price
            "time_in_force": "GTC",
            "simulate": True,
            "description": "Stop-limit order with execution price below stop",
        },
    )

    result = await dispatcher.dispatch(stop_limit_signal)
    print(f"Stop-Limit Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Stop Price: {result.get('original_order', {}).get('stop_loss')}")
    print(f"Limit Price: {result.get('original_order', {}).get('target_price')}")


async def demonstrate_take_profit_orders():
    """Demonstrate take-profit order execution"""
    print("\n" + "=" * 60)
    print("TAKE-PROFIT ORDERS")
    print("=" * 60)

    # Take profit order for a long position
    take_profit_signal = Signal(
        strategy_id="take_profit_example",
        symbol="BTCUSDT",
        action="sell",
        price=45000.0,
        confidence=0.9,
        timestamp=datetime.now(),
        meta={
            "order_type": "take_profit",
            "base_amount": 0.001,
            "take_profit": 47000.0,  # Take profit at 47k
            "use_default_take_profit": False,
            "simulate": True,
            "description": "Take profit order for long position",
        },
    )

    result = await dispatcher.dispatch(take_profit_signal)
    print(f"Take Profit Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Take Profit Price: {result.get('original_order', {}).get('take_profit')}")


async def demonstrate_take_profit_limit_orders():
    """Demonstrate take-profit-limit order execution"""
    print("\n" + "=" * 60)
    print("TAKE-PROFIT-LIMIT ORDERS")
    print("=" * 60)

    # Take profit limit order with both take profit and limit prices
    take_profit_limit_signal = Signal(
        strategy_id="take_profit_limit_example",
        symbol="BTCUSDT",
        action="sell",
        price=45000.0,
        confidence=0.8,
        timestamp=datetime.now(),
        meta={
            "order_type": "take_profit_limit",
            "base_amount": 0.001,
            "take_profit": 47000.0,  # Take profit trigger price
            "target_price": 47100.0,  # Limit execution price
            "time_in_force": "GTC",
            "simulate": True,
            "description": (
                "Take profit limit order with execution price " "above take profit"
            ),
        },
    )

    result = await dispatcher.dispatch(take_profit_limit_signal)
    print(f"Take Profit Limit Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Take Profit Price: {result.get('original_order', {}).get('take_profit')}")
    print(f"Limit Price: {result.get('original_order', {}).get('target_price')}")


async def demonstrate_advanced_signal_features():
    """Demonstrate advanced signal features"""
    print("\n" + "=" * 60)
    print("ADVANCED SIGNAL FEATURES")
    print("=" * 60)

    # Signal with custom risk management
    advanced_signal = Signal(
        strategy_id="advanced_example",
        symbol="ETHUSDT",
        action="buy",
        price=3000.0,
        confidence=0.75,
        timestamp=datetime.now(),
        meta={
            "order_type": "limit",
            "base_amount": 0.1,  # 0.1 ETH
            "stop_loss": 2850.0,  # 5% stop loss
            "take_profit": 3300.0,  # 10% take profit
            "time_in_force": "IOC",  # Immediate or Cancel
            "quote_quantity": 300.0,  # Quote quantity in USDT
            "use_default_stop_loss": False,
            "use_default_take_profit": False,
            "simulate": True,
            "description": "Advanced signal with custom risk management",
            "strategy_metadata": {
                "indicators": {"rsi": 65, "macd": "bullish"},
                "volatility": "medium",
                "trend": "uptrend",
            },
        },
    )

    result = await dispatcher.dispatch(advanced_signal)
    print(f"Advanced Signal Result: {result['status']}")
    print(f"Order ID: {result.get('order_id')}")
    print(f"Symbol: {result.get('original_order', {}).get('symbol')}")
    print(f"Order Type: {result.get('original_order', {}).get('type')}")
    print(f"Stop Loss: {result.get('original_order', {}).get('stop_loss')}")
    print(f"Take Profit: {result.get('original_order', {}).get('take_profit')}")
    print(f"Time in Force: {result.get('original_order', {}).get('time_in_force')}")


async def demonstrate_account_operations():
    """Demonstrate account and order management operations"""
    print("\n" + "=" * 60)
    print("ACCOUNT OPERATIONS")
    print("=" * 60)

    # Get account information
    try:
        account_info = await dispatcher.get_account_info()
        print(f"Account Info: {account_info.get('message', 'Retrieved')}")
        if "data" in account_info and not account_info["data"].get("simulated"):
            print(f"Can Trade: {account_info['data'].get('can_trade')}")
            print(f"Maker Commission: {account_info['data'].get('maker_commission')}")
            print(f"Taker Commission: {account_info['data'].get('taker_commission')}")
    except Exception as e:
        print(f"Account info error: {e}")

    # Get symbol price
    try:
        price = await dispatcher.get_symbol_price("BTCUSDT")
        print(f"BTCUSDT Price: ${price:,.2f}")
    except Exception as e:
        print(f"Price error: {e}")


async def demonstrate_live_vs_simulation():
    """Demonstrate the difference between live and simulation trading"""
    print("\n" + "=" * 60)
    print("LIVE VS SIMULATION TRADING")
    print("=" * 60)

    print(f"Simulation Enabled: {SIMULATION_ENABLED}")
    print(f"Binance Testnet: {BINANCE_TESTNET}")

    if SIMULATION_ENABLED:
        print("✅ Currently running in SIMULATION mode")
        print("   - All orders are simulated")
        print("   - No real money is at risk")
        print("   - Perfect for testing strategies")
    else:
        print("⚠️  Currently running in LIVE trading mode")
        print("   - Real orders will be placed on Binance")
        print("   - Real money is at risk")
        print("   - Use with caution!")

    if BINANCE_TESTNET:
        print("✅ Using Binance Testnet")
        print("   - Safe testing environment")
        print("   - No real money involved")
    else:
        print("⚠️  Using Binance Mainnet")
        print("   - Real trading environment")
        print("   - Real money involved")


async def demonstrate_hold_signals():
    """Demonstrate hold signal handling"""
    print("\n" + "=" * 60)
    print("HOLD SIGNALS")
    print("=" * 60)

    # Hold signal (no action)
    hold_signal = Signal(
        strategy_id="hold_example",
        symbol="BTCUSDT",
        action="hold",
        price=45000.0,
        confidence=0.5,
        timestamp=datetime.now(),
        meta={
            "description": "Hold signal - no trading action",
            "reason": "Market conditions uncertain",
        },
    )

    result = await dispatcher.dispatch(hold_signal)
    print(f"Hold Signal Result: {result['status']}")
    print(f"Message: {result.get('message')}")


async def main():
    """Run all trading demonstrations"""
    print("🚀 Petrosa Trading Engine - Advanced Trading Example")
    print("=" * 80)

    try:
        # Run all demonstrations
        await demonstrate_market_orders()
        await demonstrate_limit_orders()
        await demonstrate_stop_orders()
        await demonstrate_stop_limit_orders()
        await demonstrate_take_profit_orders()
        await demonstrate_take_profit_limit_orders()
        await demonstrate_advanced_signal_features()
        await demonstrate_account_operations()
        await demonstrate_live_vs_simulation()
        await demonstrate_hold_signals()

        print("\n" + "=" * 80)
        print("✅ Advanced Trading Example Complete!")
        print("=" * 80)

        print("\nKey Features Demonstrated:")
        print("1. ✅ Market Orders - Immediate execution at current price")
        print("2. ✅ Limit Orders - Execution at specified price or better")
        print("3. ✅ Stop Orders - Market execution when price hits stop level")
        print("4. ✅ Stop-Limit Orders - Limit execution when price hits stop level")
        print("5. ✅ Take-Profit Orders - Market execution at profit target")
        print("6. ✅ Take-Profit-Limit Orders - Limit execution at profit target")
        print("7. ✅ Advanced Risk Management - Stop loss and take profit")
        print("8. ✅ Account Operations - Balance and price queries")
        print("9. ✅ Live vs Simulation - Safe testing environment")
        print("10. ✅ Hold Signals - No-action signals")

        print("\nNext Steps:")
        print("1. Set your Binance API keys in .env file")
        print("2. Set SIMULATION_ENABLED=false for live trading")
        print("3. Set BINANCE_TESTNET=false for mainnet trading")
        print("4. Test with small amounts first!")

    except Exception as e:
        print(f"❌ Error running trading example: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
