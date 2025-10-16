#!/usr/bin/env python3
"""
Query Binance API to understand how positions and SL/TP orders appear

This script queries multiple Binance endpoints to see how positions and their
associated SL/TP orders are represented in different API responses.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from binance import Client

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BinancePositionQuery:
    """Query Binance for positions and orders in different ways"""

    def __init__(self):
        """Initialize Binance client"""
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

        if not api_key or not api_secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")

        if testnet:
            self.client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
            logger.info("🔧 Using Binance TESTNET")
        else:
            self.client = Client(api_key=api_key, api_secret=api_secret)
            logger.info("⚠️  Using Binance PRODUCTION")

        self.symbol = "BTCUSDT"

    async def query_position_mode(self) -> dict[str, Any]:
        """Query position mode (hedge or one-way)"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info("🔍 QUERYING POSITION MODE")
            logger.info(f"{'='*80}")

            position_mode = self.client.futures_get_position_mode()
            logger.info("Position Mode Response:")
            logger.info(json.dumps(position_mode, indent=2))

            dual_side = position_mode.get("dualSidePosition", False)
            mode = "HEDGE MODE" if dual_side else "ONE-WAY MODE"
            logger.info(f"\n📊 Account is in: {mode}")

            return position_mode
        except Exception as e:
            logger.error(f"❌ Error querying position mode: {e}")
            return {}

    async def query_position_information(self) -> list[dict[str, Any]]:
        """Query position information (shows current positions)"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🔍 QUERYING POSITION INFORMATION - {self.symbol}")
            logger.info(f"{'='*80}")

            positions = self.client.futures_position_information(symbol=self.symbol)

            logger.info(f"\nFound {len(positions)} position records:")
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    logger.info("\n📍 OPEN POSITION:")
                else:
                    logger.info("\n📍 Empty Position Slot:")

                logger.info(f"  Position Side: {pos.get('positionSide')}")
                logger.info(f"  Position Amount: {pos.get('positionAmt')}")
                logger.info(f"  Entry Price: {pos.get('entryPrice')}")
                logger.info(f"  Mark Price: {pos.get('markPrice')}")
                logger.info(f"  Unrealized PnL: {pos.get('unRealizedProfit')}")
                logger.info(f"  Leverage: {pos.get('leverage')}")
                logger.info(f"  Isolated Wallet: {pos.get('isolatedWallet')}")

            logger.info("\n📋 Full Response:")
            logger.info(json.dumps(positions, indent=2))

            return positions
        except Exception as e:
            logger.error(f"❌ Error querying positions: {e}")
            return []

    async def query_open_orders(self) -> list[dict[str, Any]]:
        """Query open orders (shows all open orders)"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🔍 QUERYING OPEN ORDERS - {self.symbol}")
            logger.info(f"{'='*80}")

            orders = self.client.futures_get_open_orders(symbol=self.symbol)

            logger.info(f"\nFound {len(orders)} open orders:")
            for order in orders:
                logger.info(f"\n📋 Order {order.get('orderId')}:")
                logger.info(f"  Type: {order.get('type')}")
                logger.info(f"  Side: {order.get('side')}")
                logger.info(f"  Position Side: {order.get('positionSide')}")
                logger.info(f"  Status: {order.get('status')}")
                logger.info(f"  Quantity: {order.get('origQty')}")
                logger.info(f"  Price: {order.get('price')}")
                logger.info(f"  Stop Price: {order.get('stopPrice')}")
                logger.info(f"  Reduce Only: {order.get('reduceOnly')}")
                logger.info(f"  Close Position: {order.get('closePosition')}")
                logger.info(f"  Time in Force: {order.get('timeInForce')}")
                logger.info(f"  Working Type: {order.get('workingType')}")

            if orders:
                logger.info("\n📋 Full Response:")
                logger.info(json.dumps(orders, indent=2))
            else:
                logger.info("\n⚠️  No open orders found")

            return orders
        except Exception as e:
            logger.error(f"❌ Error querying open orders: {e}")
            return []

    async def query_position_risk(self) -> list[dict[str, Any]]:
        """Query position risk (V2 endpoint - shows positions with more details)"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🔍 QUERYING POSITION RISK - {self.symbol}")
            logger.info(f"{'='*80}")

            # This uses the V2 endpoint which has more details
            risk = self.client.futures_position_information(symbol=self.symbol)

            logger.info("\nPosition Risk Details:")
            for pos in risk:
                logger.info(f"\n📊 Position Side: {pos.get('positionSide')}")
                logger.info(f"  Position Amount: {pos.get('positionAmt')}")
                logger.info(f"  Entry Price: {pos.get('entryPrice')}")
                logger.info(f"  Mark Price: {pos.get('markPrice')}")
                logger.info(f"  Unrealized Profit: {pos.get('unRealizedProfit')}")
                logger.info(f"  Liquidation Price: {pos.get('liquidationPrice')}")
                logger.info(f"  Leverage: {pos.get('leverage')}")
                logger.info(f"  Max Notional: {pos.get('maxNotionalValue')}")
                logger.info(f"  Margin Type: {pos.get('marginType')}")
                logger.info(f"  Isolated Margin: {pos.get('isolatedMargin')}")
                logger.info(f"  Is Auto Add Margin: {pos.get('isAutoAddMargin')}")
                logger.info(f"  Position Side: {pos.get('positionSide')}")

            logger.info("\n📋 Full Response:")
            logger.info(json.dumps(risk, indent=2))

            return risk
        except Exception as e:
            logger.error(f"❌ Error querying position risk: {e}")
            return []

    async def query_account_information(self) -> dict[str, Any]:
        """Query account information (shows overall account state)"""
        try:
            logger.info(f"\n{'='*80}")
            logger.info("🔍 QUERYING ACCOUNT INFORMATION")
            logger.info(f"{'='*80}")

            account = self.client.futures_account()

            logger.info("\n💰 Account Overview:")
            logger.info(f"  Total Wallet Balance: {account.get('totalWalletBalance')}")
            logger.info(
                f"  Total Unrealized Profit: {account.get('totalUnrealizedProfit')}"
            )
            logger.info(f"  Total Margin Balance: {account.get('totalMarginBalance')}")
            logger.info(f"  Available Balance: {account.get('availableBalance')}")
            logger.info(f"  Max Withdraw Amount: {account.get('maxWithdrawAmount')}")

            # Show positions from account endpoint
            positions = account.get("positions", [])
            btc_positions = [p for p in positions if p.get("symbol") == self.symbol]

            logger.info(f"\n📍 {self.symbol} Positions from Account:")
            for pos in btc_positions:
                logger.info(f"\n  Position Side: {pos.get('positionSide')}")
                logger.info(f"  Position Amount: {pos.get('positionAmt')}")
                logger.info(f"  Entry Price: {pos.get('entryPrice')}")
                logger.info(f"  Unrealized Profit: {pos.get('unrealizedProfit')}")

            # Note: Not logging full account response as it contains sensitive data
            logger.info("\n(Full account response omitted for security)")

            return account
        except Exception as e:
            logger.error(f"❌ Error querying account: {e}")
            return {}

    async def check_how_ui_sees_sltp(self) -> None:
        """
        Check how the Binance UI would see SL/TP orders

        The Binance UI typically shows SL/TP in the position view by:
        1. Checking open orders for STOP_MARKET and TAKE_PROFIT_MARKET types
        2. Filtering by positionSide matching the position
        3. Checking reduceOnly=true flag
        """
        logger.info(f"\n{'='*80}")
        logger.info("🎨 HOW BINANCE UI DISPLAYS SL/TP")
        logger.info(f"{'='*80}")

        try:
            # Get positions
            positions = self.client.futures_position_information(symbol=self.symbol)
            open_positions = [
                p for p in positions if float(p.get("positionAmt", 0)) != 0
            ]

            # Get open orders
            orders = self.client.futures_get_open_orders(symbol=self.symbol)

            logger.info("\n🔍 Analysis of SL/TP Display:")

            for pos in open_positions:
                pos_side = pos.get("positionSide")
                pos_amt = pos.get("positionAmt")

                logger.info(f"\n📍 Position: {pos_side} {pos_amt}")

                # Find SL/TP orders for this position
                sl_orders = [
                    o
                    for o in orders
                    if o.get("type") == "STOP_MARKET"
                    and o.get("positionSide") == pos_side
                    and o.get("reduceOnly") is True
                ]

                tp_orders = [
                    o
                    for o in orders
                    if o.get("type") == "TAKE_PROFIT_MARKET"
                    and o.get("positionSide") == pos_side
                    and o.get("reduceOnly") is True
                ]

                if sl_orders:
                    logger.info(f"  ✅ Stop Loss Orders Found: {len(sl_orders)}")
                    for sl in sl_orders:
                        logger.info(
                            f"    - Order {sl.get('orderId')}: Stop @ {sl.get('stopPrice')}"
                        )
                else:
                    logger.info("  ❌ No Stop Loss Orders")

                if tp_orders:
                    logger.info(f"  ✅ Take Profit Orders Found: {len(tp_orders)}")
                    for tp in tp_orders:
                        logger.info(
                            f"    - Order {tp.get('orderId')}: TP @ {tp.get('stopPrice')}"
                        )
                else:
                    logger.info("  ❌ No Take Profit Orders")

                # This is what the UI would show
                if sl_orders or tp_orders:
                    logger.info("\n  🎨 Binance UI would show:")
                    sl_price = sl_orders[0].get("stopPrice") if sl_orders else "--"
                    tp_price = tp_orders[0].get("stopPrice") if tp_orders else "--"
                    logger.info(f"    SL/TP: {sl_price} / {tp_price}")
                else:
                    logger.info("\n  🎨 Binance UI would show:")
                    logger.info("    SL/TP: -- / --")

        except Exception as e:
            logger.error(f"❌ Error analyzing UI display: {e}")

    async def run_all_queries(self):
        """Run all queries"""
        logger.info(f"\n{'#'*80}")
        logger.info("# BINANCE API POSITION & ORDER QUERY")
        logger.info(f"{'#'*80}\n")

        # Query all endpoints
        await self.query_position_mode()
        await self.query_position_information()
        await self.query_open_orders()
        await self.query_position_risk()
        await self.query_account_information()
        await self.check_how_ui_sees_sltp()

        logger.info(f"\n{'#'*80}")
        logger.info("# QUERY COMPLETE")
        logger.info(f"{'#'*80}\n")


async def main():
    """Main entry point"""
    try:
        querier = BinancePositionQuery()
        await querier.run_all_queries()
    except KeyboardInterrupt:
        logger.info("\n⚠️  Query interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
