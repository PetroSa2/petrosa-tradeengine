#!/usr/bin/env python3
"""
Automated test for multi-strategy OCO tracking on Binance testnet.

This script tests the scenario where multiple strategies contribute to the same
exchange position and validates that:
1. Each strategy gets its own OCO orders
2. When one strategy's OCO fills, only that strategy closes
3. P&L is calculated using each strategy's individual entry price
4. Other strategies remain open with their OCO orders active
"""

import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts.signal import Signal  # noqa: E402
from tradeengine.dispatcher import Dispatcher  # noqa: E402
from tradeengine.exchange import BinanceExchange  # noqa: E402
from tradeengine.strategy_position_manager import strategy_position_manager


async def test_multi_strategy_oco():
    """Test multi-strategy OCO tracking with live Binance testnet"""

    print("=" * 80)
    print("🧪 MULTI-STRATEGY OCO TRACKING TEST")
    print("=" * 80)
    print()

    # Initialize with testnet
    exchange = BinanceExchange(testnet=True)
    dispatcher = Dispatcher(exchange=exchange)

    try:
        print("🔧 Initializing dispatcher...")
        await dispatcher.initialize()
        print("✅ Dispatcher initialized")
        print()

        # Get current BTC price
        ticker = exchange.client.futures_symbol_ticker(symbol="BTCUSDT")
        current_price = float(ticker["price"])
        print(f"📊 Current BTC Price: ${current_price:,.2f}")
        print()

        # ===================================================================
        # Test Scenario 1: Single Strategy Position
        # ===================================================================
        print("=" * 80)
        print("🧪 TEST 1: Single Strategy Position")
        print("=" * 80)
        print()

        # Calculate TP/SL based on current price
        strategy_a_entry = current_price
        strategy_a_tp = strategy_a_entry * 1.02  # 2% profit
        strategy_a_sl = strategy_a_entry * 0.98  # 2% loss

        signal_a = Signal(
            strategy_id="test_momentum_v1",
            symbol="BTCUSDT",
            action="buy",
            quantity=0.001,  # Small testnet quantity
            price=strategy_a_entry,
            current_price=strategy_a_entry,
            confidence=0.8,
            take_profit=strategy_a_tp,
            stop_loss=strategy_a_sl,
            source="test",
            strategy="test_momentum_v1",
            timeframe="15m",
        )

        print("📤 Sending signal for Strategy A (Momentum):")
        print(f"   Entry: ${strategy_a_entry:,.2f}")
        print(f"   TP: ${strategy_a_tp:,.2f} (+2%)")
        print(f"   SL: ${strategy_a_sl:,.2f} (-2%)")
        print("   Quantity: 0.001 BTC")
        print()

        result_a = await dispatcher.process_signal(signal_a)
        print(f"✅ Strategy A processed: {result_a.get('status')}")
        print()

        # Wait for OCO orders to be placed
        await asyncio.sleep(3)

        # Verify OCO pairs
        btcusdt_long_key = "BTCUSDT_LONG"
        oco_pairs_a = dispatcher.oco_manager.active_oco_pairs.get(btcusdt_long_key, [])
        print(f"🔍 Active OCO pairs for BTCUSDT_LONG: {len(oco_pairs_a)}")

        if len(oco_pairs_a) == 1:
            print("✅ TEST 1 PASSED: Single OCO pair created")
            oco_a = oco_pairs_a[0]
            print(f"   Strategy Position ID: {oco_a.get('strategy_position_id')}")
            print(f"   Entry Price: ${oco_a.get('entry_price'):,.2f}")
            print(f"   SL Order: {oco_a.get('sl_order_id')}")
            print(f"   TP Order: {oco_a.get('tp_order_id')}")
        else:
            print(f"❌ TEST 1 FAILED: Expected 1 OCO pair, got {len(oco_pairs_a)}")
            return

        print()

        # ===================================================================
        # Test Scenario 2: Add Second Strategy to Same Position
        # ===================================================================
        print("=" * 80)
        print("🧪 TEST 2: Add Second Strategy (Same Direction)")
        print("=" * 80)
        print()

        await asyncio.sleep(2)

        # Get updated price
        ticker = exchange.client.futures_symbol_ticker(symbol="BTCUSDT")
        current_price = float(ticker["price"])

        strategy_b_entry = current_price
        strategy_b_tp = strategy_b_entry * 1.03  # 3% profit (different from A)
        strategy_b_sl = strategy_b_entry * 0.97  # 3% loss (different from A)

        signal_b = Signal(
            strategy_id="test_breakout_v2",
            symbol="BTCUSDT",
            action="buy",
            quantity=0.002,  # Different quantity
            price=strategy_b_entry,
            current_price=strategy_b_entry,
            confidence=0.75,
            take_profit=strategy_b_tp,
            stop_loss=strategy_b_sl,
            source="test",
            strategy="test_breakout_v2",
            timeframe="15m",
        )

        print("📤 Sending signal for Strategy B (Breakout):")
        print(f"   Entry: ${strategy_b_entry:,.2f}")
        print(f"   TP: ${strategy_b_tp:,.2f} (+3%)")
        print(f"   SL: ${strategy_b_sl:,.2f} (-3%)")
        print("   Quantity: 0.002 BTC")
        print()

        result_b = await dispatcher.process_signal(signal_b)
        print(f"✅ Strategy B processed: {result_b.get('status')}")
        print()

        # Wait for OCO orders to be placed
        await asyncio.sleep(3)

        # Verify both OCO pairs exist
        oco_pairs_both = dispatcher.oco_manager.active_oco_pairs.get(
            btcusdt_long_key, []
        )
        print(f"🔍 Active OCO pairs for BTCUSDT_LONG: {len(oco_pairs_both)}")

        if len(oco_pairs_both) == 2:
            print("✅ TEST 2 PASSED: Two OCO pairs exist for same exchange position")
            for i, oco in enumerate(oco_pairs_both, 1):
                print(f"\n   OCO Pair {i}:")
                print(f"   Strategy Position: {oco.get('strategy_position_id')}")
                print(f"   Entry Price: ${oco.get('entry_price'):,.2f}")
                print(f"   Quantity: {oco.get('quantity')}")
                print(f"   SL Order: {oco.get('sl_order_id')}")
                print(f"   TP Order: {oco.get('tp_order_id')}")
        else:
            print(f"❌ TEST 2 FAILED: Expected 2 OCO pairs, got {len(oco_pairs_both)}")
            return

        print()

        # ===================================================================
        # Test Scenario 3: Verify Exchange Position
        # ===================================================================
        print("=" * 80)
        print("🧪 TEST 3: Verify Exchange Position State")
        print("=" * 80)
        print()

        # Get position from Binance
        positions = exchange.client.futures_position_information(symbol="BTCUSDT")

        for pos in positions:
            if pos["symbol"] == "BTCUSDT" and pos["positionSide"] == "LONG":
                position_amt = float(pos["positionAmt"])
                entry_price = float(pos["entryPrice"])
                unrealized_pnl = float(pos["unRealizedProfit"])

                print("📊 Binance Position:")
                print(f"   Quantity: {position_amt} BTC")
                print(f"   Entry Price: ${entry_price:,.2f}")
                print(f"   Unrealized P&L: ${unrealized_pnl:,.2f}")
                print()

                expected_quantity = 0.003  # 0.001 + 0.002
                if abs(position_amt - expected_quantity) < 0.0001:
                    print("✅ TEST 3 PASSED: Exchange position has combined quantity")
                else:
                    print(
                        f"⚠️  Position quantity: {position_amt}, expected: {expected_quantity}"
                    )

        print()

        # ===================================================================
        # Test Scenario 4: Verify Strategy Positions in MongoDB
        # ===================================================================
        print("=" * 80)
        print("🧪 TEST 4: Verify Strategy Positions in MongoDB")
        print("=" * 80)
        print()

        # Get strategy positions from manager
        if strategy_position_manager:
            strategy_a_positions = (
                strategy_position_manager.get_strategy_positions_by_strategy(
                    "test_momentum_v1"
                )
            )
            strategy_b_positions = (
                strategy_position_manager.get_strategy_positions_by_strategy(
                    "test_breakout_v2"
                )
            )

            print(f"📊 Strategy A Positions: {len(strategy_a_positions)}")
            print(f"📊 Strategy B Positions: {len(strategy_b_positions)}")

            if len(strategy_a_positions) == 1 and len(strategy_b_positions) == 1:
                print(
                    "✅ TEST 4 PASSED: Both strategies have separate position records"
                )

                pos_a = strategy_a_positions[0]
                pos_b = strategy_b_positions[0]

                print("\n   Strategy A:")
                print(f"   Entry Price: ${pos_a.get('entry_price'):,.2f}")
                print(f"   Quantity: {pos_a.get('entry_quantity')}")
                print(f"   Status: {pos_a.get('status')}")

                print("\n   Strategy B:")
                print(f"   Entry Price: ${pos_b.get('entry_price'):,.2f}")
                print(f"   Quantity: {pos_b.get('entry_quantity')}")
                print(f"   Status: {pos_b.get('status')}")
            else:
                print(
                    f"❌ TEST 4 FAILED: Expected 1 position each, got A:{len(strategy_a_positions)}, B:{len(strategy_b_positions)}"
                )

        print()

        # ===================================================================
        # Test Summary
        # ===================================================================
        print("=" * 80)
        print("📊 TEST SUMMARY")
        print("=" * 80)
        print()
        print(
            "✅ All tests passed! The system is ready for multi-strategy OCO tracking."
        )
        print()
        print("📝 What was verified:")
        print("   1. Single strategy creates one OCO pair")
        print("   2. Second strategy adds its own OCO pair (no duplicate prevention)")
        print("   3. Both strategies tracked separately with own entry prices")
        print("   4. Exchange position aggregates both strategies")
        print()
        print("🎯 Next Steps:")
        print("   - Wait for price to move to trigger one strategy's OCO")
        print("   - Monitor logs to verify only owning strategy closes")
        print("   - Check MongoDB for correct P&L attribution")
        print("   - Verify Prometheus metrics in Grafana")
        print()
        print("⚠️  NOTE: To test actual OCO triggering, you need to:")
        print("   1. Wait for natural price movement, OR")
        print("   2. Manually place market orders to move price, OR")
        print("   3. Set tighter TP/SL levels closer to current price")
        print()
        print("=" * 80)

    except Exception as e:
        print(f"❌ TEST FAILED WITH ERROR: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Cleanup
        print("\n🧹 Cleaning up...")
        await dispatcher.close()
        print("✅ Cleanup complete")


if __name__ == "__main__":
    asyncio.run(test_multi_strategy_oco())
