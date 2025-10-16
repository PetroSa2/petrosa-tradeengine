#!/usr/bin/env python3
"""
Test real order creation on Binance Futures testnet with proper minimum notional

This validates that we can create, query, and cancel real orders on the testnet.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance import Client  # noqa: E402


async def test_real_order():
    """Test real order creation with proper notional value"""
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    print("🚀 Testing Real Order Creation on Binance Futures Testnet")
    print("=" * 70)

    try:
        # Initialize client
        client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
        print("✅ Client initialized")

        # Get current price
        ticker = client.futures_symbol_ticker(symbol="BTCUSDT")
        current_price = float(ticker["price"])
        print(f"📊 Current BTCUSDT price: ${current_price:,.2f}")

        # Calculate order with minimum notional of $100
        # At 50% of current price to ensure it doesn't fill
        limit_price = round(current_price * 0.5, 1)  # 50% of current price
        min_notional = 100  # Minimum required
        quantity = round((min_notional / limit_price) * 1.1, 3)  # 10% above minimum

        notional_value = limit_price * quantity

        print("\n📝 Creating LIMIT BUY order:")
        print("  Symbol: BTCUSDT")
        print(f"  Quantity: {quantity} BTC")
        print(f"  Price: ${limit_price:,.2f}")
        print(f"  Notional Value: ${notional_value:,.2f}")
        print("  Min Required: $100.00")

        # Create the order
        order = client.futures_create_order(
            symbol="BTCUSDT",
            side="BUY",
            type="LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            price=str(limit_price),
        )

        order_id = order["orderId"]
        print("\n✅ Order created successfully!")
        print(f"  Order ID: {order_id}")
        print(f"  Status: {order['status']}")
        print(f"  Client Order ID: {order.get('clientOrderId', 'N/A')}")

        # Query the order
        print("\n🔍 Querying order status...")
        order_status = client.futures_get_order(symbol="BTCUSDT", orderId=order_id)
        print("✅ Order queried successfully")
        print(f"  Status: {order_status['status']}")
        print(f"  Type: {order_status['type']}")
        print(f"  Side: {order_status['side']}")
        print(f"  Executed Qty: {order_status['executedQty']}")

        # Cancel the order
        print("\n❌ Cancelling order...")
        cancel_result = client.futures_cancel_order(symbol="BTCUSDT", orderId=order_id)
        print("✅ Order cancelled successfully")
        print(f"  Final Status: {cancel_result['status']}")

        print("\n" + "=" * 70)
        print("🎉 All real order tests PASSED!")
        print("✅ Can create, query, and cancel orders on Binance Futures testnet")
        print("=" * 70)

        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(test_real_order())
    sys.exit(0 if result else 1)
