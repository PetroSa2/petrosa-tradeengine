import asyncio
import logging

from binance import Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Liquidation")


async def close_all():
    api_key = "VGK4c8SSNZtS7ATDd8ClzdgGFEffvGnaAc471nzYF43lfsBZKBcfbGTaSQFGiF0v"
    api_secret = "2NoOvkvnbpdf9qmAYlivh07Gt7t786IFkMgy20NolUKdaNpSGo2h01nNPi7cw9QH"

    client = Client(api_key, api_secret, testnet=True)

    # 1. Cancel all open orders
    logger.info("Cancelling all open orders...")
    try:
        open_orders = client.futures_get_open_orders()
        for order in open_orders:
            client.futures_cancel_order(
                symbol=order["symbol"], orderId=order["orderId"]
            )
            logger.info(f"Cancelled order {order['orderId']} for {order['symbol']}")
    except Exception as e:
        logger.error(f"Error cancelling orders: {e}")

    # 2. Close all positions
    logger.info("Fetching active positions...")
    positions = client.futures_position_information()
    active_positions = [p for p in positions if float(p["positionAmt"]) != 0]

    if not active_positions:
        logger.info("No active positions to close.")
        return

    logger.info(f"Found {len(active_positions)} positions. Closing...")

    for p in active_positions:
        symbol = p["symbol"]
        amt = float(p["positionAmt"])
        side = p["positionSide"]  # Hedge mode aware

        # Determine closing action
        # If amt > 0 (Long), we need to SELL
        # If amt < 0 (Short), we need to BUY
        close_side = "SELL" if amt > 0 else "BUY"
        close_qty = abs(amt)

        logger.info(f"Closing {symbol} {side}: {amt} units...")

        try:
            # IN HEDGE MODE: Do NOT send reduceOnly=True.
            # Specifying positionSide already tells Binance which side to reduce.
            res = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=close_qty,
                positionSide=side,
            )
            logger.info(f"✅ Closed {symbol}: {res.get('orderId')}")
        except Exception as e:
            logger.error(f"❌ Failed to close {symbol} {side}: {e}")

    # 3. Final Balance Check
    account = client.futures_account()
    for asset in account.get("assets", []):
        if asset["asset"] == "USDT":
            logger.info(f"💰 FINAL USDT Available Balance: {asset['availableBalance']}")


if __name__ == "__main__":
    asyncio.run(close_all())
