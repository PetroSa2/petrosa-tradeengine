#!/usr/bin/env python3
"""
Test script for SL/TP order placement in Binance Futures Hedge Mode

This script tests different approaches to placing stop-loss and take-profit orders
to identify why they're not appearing on Binance when placed by the trading engine.

Run in K8s pod:
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps deployment/petrosa-tradeengine -- python scripts/test_sl_tp_hedge_mode.py
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from binance import Client
from binance.enums import (
    FUTURE_ORDER_TYPE_MARKET,
    FUTURE_ORDER_TYPE_STOP_MARKET,
    FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
    SIDE_BUY,
    SIDE_SELL,
)
from binance.exceptions import BinanceAPIException

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BinanceSLTPTester:
    """Test SL/TP order placement strategies on Binance Futures"""

    def __init__(self):
        """Initialize Binance client"""
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

        if not api_key or not api_secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")

        if testnet:
            logger.info("🔧 Using Binance TESTNET")
            self.client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
        else:
            logger.info("⚠️  Using Binance PRODUCTION")
            self.client = Client(api_key=api_key, api_secret=api_secret)

        # Test configuration
        self.symbol = "BTCUSDT"
        self.test_quantity = 0.001  # Small test quantity
        self.test_position_side = "LONG"
        self.test_order_side = "BUY"
        self.position_order_id = None
        self.current_price = 0.0

    def format_price(self, price: float) -> str:
        """Format price to correct precision"""
        # BTC has 1 decimal place for futures
        return f"{price:.1f}"

    def format_quantity(self, quantity: float) -> str:
        """Format quantity to correct precision"""
        # BTC has 3 decimal places for quantity
        return f"{quantity:.3f}"

    async def get_current_price(self) -> float:
        """Get current market price"""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
            price = float(ticker["price"])
            logger.info(f"📊 Current {self.symbol} price: ${price:,.2f}")
            return price
        except Exception as e:
            logger.error(f"Failed to get current price: {e}")
            raise

    async def verify_hedge_mode(self) -> bool:
        """Verify hedge mode is enabled"""
        try:
            position_mode = self.client.futures_get_position_mode()
            dual_side = position_mode.get("dualSidePosition", False)
            logger.info(f"🔍 Hedge mode enabled: {dual_side}")
            logger.info(
                f"   Position mode response: {json.dumps(position_mode, indent=2)}"
            )
            return dual_side
        except Exception as e:
            logger.error(f"Failed to verify hedge mode: {e}")
            return False

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Get all open positions"""
        try:
            positions = self.client.futures_position_information(symbol=self.symbol)
            open_positions = [
                p for p in positions if float(p.get("positionAmt", 0)) != 0
            ]
            logger.info(f"📍 Open positions for {self.symbol}: {len(open_positions)}")
            for pos in open_positions:
                logger.info(
                    f"   {pos['positionSide']}: {pos['positionAmt']} @ ${pos['entryPrice']}"
                )
            return open_positions
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all open orders"""
        try:
            orders = self.client.futures_get_open_orders(symbol=self.symbol)
            logger.info(f"📋 Open orders for {self.symbol}: {len(orders)}")
            for order in orders:
                logger.info(
                    f"   Order {order['orderId']}: {order['type']} {order['side']} "
                    f"{order['origQty']} @ stop=${order.get('stopPrice', 'N/A')} "
                    f"status={order['status']} positionSide={order.get('positionSide', 'N/A')}"
                )
            return orders
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    async def open_test_position(self) -> dict[str, Any] | None:
        """Open a small test position"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info("📍 OPENING TEST POSITION")
            logger.info(f"{'='*80}")

            params = {
                "symbol": self.symbol,
                "side": SIDE_BUY,
                "type": FUTURE_ORDER_TYPE_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "positionSide": self.test_position_side,
            }

            logger.info(f"Parameters: {json.dumps(params, indent=2)}")

            result = self.client.futures_create_order(**params)

            logger.info("✅ Position opened successfully!")
            logger.info(f"   Order ID: {result.get('orderId')}")
            logger.info(f"   Status: {result.get('status')}")
            logger.info(f"   Full response: {json.dumps(result, indent=2)}")

            self.position_order_id = result.get("orderId")

            # Wait a moment for position to settle
            await asyncio.sleep(2)

            return result

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error opening position: {e}")
            logger.error(f"   Error code: {e.code}")
            logger.error(f"   Error message: {e.message}")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to open position: {e}")
            return None

    async def test_strategy_a_current_approach(self) -> dict[str, Any]:
        """
        Test Strategy A: Current approach (reduceOnly=True only)
        This is what the current code does
        """
        logger.info(f"\n{'='*80}")
        logger.info("🧪 TEST A: Current Approach (reduceOnly=True)")
        logger.info(f"{'='*80}")

        try:
            # Calculate SL and TP prices
            sl_price = self.current_price * 0.98  # 2% below
            tp_price = self.current_price * 1.02  # 2% above

            # Stop Loss Order
            logger.info("\n📉 Placing STOP LOSS order...")
            sl_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,  # Opposite side to close LONG
                "type": FUTURE_ORDER_TYPE_STOP_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(sl_price),
                "positionSide": self.test_position_side,  # Same as position
                "reduceOnly": True,
            }

            logger.info(f"SL Parameters: {json.dumps(sl_params, indent=2)}")
            sl_result = self.client.futures_create_order(**sl_params)
            logger.info(f"✅ Stop loss response: {json.dumps(sl_result, indent=2)}")

            # Take Profit Order
            logger.info("\n📈 Placing TAKE PROFIT order...")
            tp_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,  # Opposite side to close LONG
                "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(tp_price),
                "positionSide": self.test_position_side,  # Same as position
                "reduceOnly": True,
            }

            logger.info(f"TP Parameters: {json.dumps(tp_params, indent=2)}")
            tp_result = self.client.futures_create_order(**tp_params)
            logger.info(f"✅ Take profit response: {json.dumps(tp_result, indent=2)}")

            # Wait and verify
            await asyncio.sleep(2)
            orders = await self.get_open_orders()

            return {
                "success": True,
                "sl_order": sl_result,
                "tp_order": tp_result,
                "verification": f"{len(orders)} orders found",
            }

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error in Test A: {e}")
            logger.error(f"   Error code: {e.code}")
            logger.error(f"   Error message: {e.message}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"❌ Test A failed: {e}")
            return {"success": False, "error": str(e)}

    async def test_strategy_b_close_position(self) -> dict[str, Any]:
        """
        Test Strategy B: Add closePosition=True parameter
        Hypothesis: Binance may need explicit closePosition flag in hedge mode
        """
        logger.info(f"\n{'='*80}")
        logger.info("🧪 TEST B: With closePosition=True")
        logger.info(f"{'='*80}")

        try:
            # Calculate SL and TP prices
            sl_price = self.current_price * 0.98  # 2% below
            tp_price = self.current_price * 1.02  # 2% above

            # Stop Loss Order with closePosition
            logger.info("\n📉 Placing STOP LOSS order with closePosition...")
            sl_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_STOP_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(sl_price),
                "positionSide": self.test_position_side,
                "closePosition": True,  # NEW: explicit close position flag
            }

            logger.info(f"SL Parameters: {json.dumps(sl_params, indent=2)}")
            sl_result = self.client.futures_create_order(**sl_params)
            logger.info(f"✅ Stop loss response: {json.dumps(sl_result, indent=2)}")

            # Take Profit Order with closePosition
            logger.info("\n📈 Placing TAKE PROFIT order with closePosition...")
            tp_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(tp_price),
                "positionSide": self.test_position_side,
                "closePosition": True,  # NEW: explicit close position flag
            }

            logger.info(f"TP Parameters: {json.dumps(tp_params, indent=2)}")
            tp_result = self.client.futures_create_order(**tp_params)
            logger.info(f"✅ Take profit response: {json.dumps(tp_result, indent=2)}")

            # Wait and verify
            await asyncio.sleep(2)
            orders = await self.get_open_orders()

            return {
                "success": True,
                "sl_order": sl_result,
                "tp_order": tp_result,
                "verification": f"{len(orders)} orders found",
            }

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error in Test B: {e}")
            logger.error(f"   Error code: {e.code}")
            logger.error(f"   Error message: {e.message}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"❌ Test B failed: {e}")
            return {"success": False, "error": str(e)}

    async def test_strategy_c_no_reduce_only(self) -> dict[str, Any]:
        """
        Test Strategy C: Without reduceOnly parameter
        Hypothesis: Maybe reduceOnly conflicts with something in hedge mode
        """
        logger.info(f"\n{'='*80}")
        logger.info("🧪 TEST C: Without reduceOnly")
        logger.info(f"{'='*80}")

        try:
            # Calculate SL and TP prices
            sl_price = self.current_price * 0.98  # 2% below
            tp_price = self.current_price * 1.02  # 2% above

            # Stop Loss Order without reduceOnly
            logger.info("\n📉 Placing STOP LOSS order without reduceOnly...")
            sl_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_STOP_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(sl_price),
                "positionSide": self.test_position_side,
                # No reduceOnly parameter
            }

            logger.info(f"SL Parameters: {json.dumps(sl_params, indent=2)}")
            sl_result = self.client.futures_create_order(**sl_params)
            logger.info(f"✅ Stop loss response: {json.dumps(sl_result, indent=2)}")

            # Take Profit Order without reduceOnly
            logger.info("\n📈 Placing TAKE PROFIT order without reduceOnly...")
            tp_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "stopPrice": self.format_price(tp_price),
                "positionSide": self.test_position_side,
                # No reduceOnly parameter
            }

            logger.info(f"TP Parameters: {json.dumps(tp_params, indent=2)}")
            tp_result = self.client.futures_create_order(**tp_params)
            logger.info(f"✅ Take profit response: {json.dumps(tp_result, indent=2)}")

            # Wait and verify
            await asyncio.sleep(2)
            orders = await self.get_open_orders()

            return {
                "success": True,
                "sl_order": sl_result,
                "tp_order": tp_result,
                "verification": f"{len(orders)} orders found",
            }

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error in Test C: {e}")
            logger.error(f"   Error code: {e.code}")
            logger.error(f"   Error message: {e.message}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"❌ Test C failed: {e}")
            return {"success": False, "error": str(e)}

    async def test_strategy_d_close_position_no_quantity(self) -> dict[str, Any]:
        """
        Test Strategy D: closePosition=True WITHOUT quantity
        Hypothesis: When using closePosition, Binance might not want quantity specified
        """
        logger.info(f"\n{'='*80}")
        logger.info("🧪 TEST D: closePosition=True without quantity")
        logger.info(f"{'='*80}")

        try:
            # Calculate SL and TP prices
            sl_price = self.current_price * 0.98  # 2% below
            tp_price = self.current_price * 1.02  # 2% above

            # Stop Loss Order with closePosition, no quantity
            logger.info(
                "\n📉 Placing STOP LOSS order with closePosition, no quantity..."
            )
            sl_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_STOP_MARKET,
                "stopPrice": self.format_price(sl_price),
                "positionSide": self.test_position_side,
                "closePosition": True,
                # No quantity when closePosition=True
            }

            logger.info(f"SL Parameters: {json.dumps(sl_params, indent=2)}")
            sl_result = self.client.futures_create_order(**sl_params)
            logger.info(f"✅ Stop loss response: {json.dumps(sl_result, indent=2)}")

            # Take Profit Order with closePosition, no quantity
            logger.info(
                "\n📈 Placing TAKE PROFIT order with closePosition, no quantity..."
            )
            tp_params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,
                "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                "stopPrice": self.format_price(tp_price),
                "positionSide": self.test_position_side,
                "closePosition": True,
                # No quantity when closePosition=True
            }

            logger.info(f"TP Parameters: {json.dumps(tp_params, indent=2)}")
            tp_result = self.client.futures_create_order(**tp_params)
            logger.info(f"✅ Take profit response: {json.dumps(tp_result, indent=2)}")

            # Wait and verify
            await asyncio.sleep(2)
            orders = await self.get_open_orders()

            return {
                "success": True,
                "sl_order": sl_result,
                "tp_order": tp_result,
                "verification": f"{len(orders)} orders found",
            }

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error in Test D: {e}")
            logger.error(f"   Error code: {e.code}")
            logger.error(f"   Error message: {e.message}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"❌ Test D failed: {e}")
            return {"success": False, "error": str(e)}

    async def close_test_position(self) -> bool:
        """Close the test position"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info("🧹 CLOSING TEST POSITION")
            logger.info(f"{'='*80}")

            # First, cancel all open orders
            logger.info("Cancelling all open orders...")
            try:
                cancel_result = self.client.futures_cancel_all_open_orders(
                    symbol=self.symbol
                )
                logger.info(f"Cancelled orders: {cancel_result}")
            except Exception as e:
                logger.warning(f"Error cancelling orders: {e}")

            # Close position
            params = {
                "symbol": self.symbol,
                "side": SIDE_SELL,  # Opposite of opening trade
                "type": FUTURE_ORDER_TYPE_MARKET,
                "quantity": self.format_quantity(self.test_quantity),
                "positionSide": self.test_position_side,
                "reduceOnly": True,
            }

            logger.info(f"Close parameters: {json.dumps(params, indent=2)}")
            result = self.client.futures_create_order(**params)

            logger.info("✅ Position closed successfully!")
            logger.info(f"   Close order ID: {result.get('orderId')}")

            await asyncio.sleep(2)
            return True

        except BinanceAPIException as e:
            logger.error(f"❌ Binance API error closing position: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to close position: {e}")
            return False

    async def run_all_tests(self):
        """Run all test strategies"""
        logger.info(f"\n{'#'*80}")
        logger.info("# BINANCE SL/TP TEST SUITE - HEDGE MODE")
        logger.info(f"{'#'*80}\n")

        # Verify hedge mode
        if not await self.verify_hedge_mode():
            logger.error(
                "❌ Hedge mode is not enabled! Please enable it in Binance settings."
            )
            return

        # Get current price
        self.current_price = await self.get_current_price()

        # Check existing positions/orders
        await self.get_open_positions()
        await self.get_open_orders()

        # Ask for confirmation
        logger.info(f"\n{'='*80}")
        logger.info("⚠️  READY TO START TESTS")
        logger.info(f"   Symbol: {self.symbol}")
        logger.info(f"   Quantity: {self.test_quantity}")
        logger.info(f"   Approx value: ${self.current_price * self.test_quantity:.2f}")
        logger.info(f"{'='*80}\n")

        # Run tests
        results = {}

        # Test A: Current approach
        await self.open_test_position()
        results["test_a"] = await self.test_strategy_a_current_approach()
        await self.close_test_position()
        await asyncio.sleep(3)

        # Test B: With closePosition
        await self.open_test_position()
        results["test_b"] = await self.test_strategy_b_close_position()
        await self.close_test_position()
        await asyncio.sleep(3)

        # Test C: Without reduceOnly
        await self.open_test_position()
        results["test_c"] = await self.test_strategy_c_no_reduce_only()
        await self.close_test_position()
        await asyncio.sleep(3)

        # Test D: closePosition without quantity
        await self.open_test_position()
        results["test_d"] = await self.test_strategy_d_close_position_no_quantity()
        await self.close_test_position()
        await asyncio.sleep(3)

        # Final summary
        logger.info(f"\n{'#'*80}")
        logger.info("# TEST RESULTS SUMMARY")
        logger.info(f"{'#'*80}\n")

        for test_name, result in results.items():
            status = "✅ SUCCESS" if result.get("success") else "❌ FAILED"
            logger.info(f"{test_name.upper()}: {status}")
            if not result.get("success"):
                logger.info(f"   Error: {result.get('error', 'Unknown')}")
                logger.info(f"   Code: {result.get('code', 'N/A')}")
            else:
                logger.info(f"   {result.get('verification', 'No verification info')}")

        logger.info(f"\n{'#'*80}")
        logger.info("# FINAL STATUS CHECK")
        logger.info(f"{'#'*80}\n")

        await self.get_open_positions()
        await self.get_open_orders()


async def main():
    """Main entry point"""
    try:
        tester = BinanceSLTPTester()
        await tester.run_all_tests()
    except KeyboardInterrupt:
        logger.info("\n⚠️  Test interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
