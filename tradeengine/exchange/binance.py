"""
Binance Futures Exchange Client - Petrosa Trading Engine

This module provides a comprehensive interface to Binance Futures API for executing
all types of trading orders including market, limit, stop, and take-profit orders.
"""

import asyncio
import logging
import time
from typing import Any

from binance import Client
from binance.enums import (
    FUTURE_ORDER_TYPE_LIMIT,
    FUTURE_ORDER_TYPE_MARKET,
    FUTURE_ORDER_TYPE_STOP,
    FUTURE_ORDER_TYPE_STOP_MARKET,
    FUTURE_ORDER_TYPE_TAKE_PROFIT,
    FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
    SIDE_BUY,
    SIDE_SELL,
    TIME_IN_FORCE_GTC,
)
from binance.exceptions import BinanceAPIException

from contracts.order import TradeOrder
from shared.constants import MAX_RETRY_ATTEMPTS, RETRY_BACKOFF_MULTIPLIER, RETRY_DELAY

logger = logging.getLogger(__name__)


class BinanceFuturesExchange:
    """Binance Futures exchange client for executing trades"""

    def __init__(self) -> None:
        self.client: Client | None = None
        self.exchange_info: dict[str, Any] = {}
        self.symbol_info: dict[str, Any] = {}
        self.initialized = False

    async def initialize(self) -> None:
        """Initialize Binance Futures exchange connection"""
        try:
            # Import constants here to avoid circular imports
            from shared.constants import (
                BINANCE_API_KEY,
                BINANCE_API_SECRET,
                BINANCE_TESTNET,
            )

            # Debug logging
            logger.info(
                f"Binance Futures initialization - API_KEY present: {bool(BINANCE_API_KEY)}"
            )
            logger.info(
                f"Binance Futures initialization - API_SECRET present: "
                f"{bool(BINANCE_API_SECRET)}"
            )
            logger.info(f"Binance Futures initialization - TESTNET: {BINANCE_TESTNET}")

            # Create Binance Futures client
            if BINANCE_API_KEY and BINANCE_API_SECRET:
                logger.info("Creating Binance Futures UMFutures client...")
                self.client = Client(
                    api_key=BINANCE_API_KEY,
                    api_secret=BINANCE_API_SECRET,
                    testnet=BINANCE_TESTNET,
                )
                logger.info(
                    f"Binance Futures client created (testnet: {BINANCE_TESTNET})"
                )

                # Test connection
                logger.info("Testing Binance Futures connection...")
                self.client.futures_ping()
                logger.info("Binance Futures connection test successful")

                # Load exchange info
                logger.info("Loading futures exchange info...")
                await self._load_exchange_info()

                self.initialized = True
                logger.info("Binance Futures exchange initialized successfully")
            else:
                logger.warning(
                    "Binance API credentials not provided, client not initialized"
                )
                self.client = None
                self.initialized = False
        except Exception as e:
            logger.error(f"Binance Futures initialization error: {e}")
            self.client = None
            self.initialized = False
            raise

    async def health_check(self) -> dict[str, Any]:
        """Check Binance Futures exchange health"""
        try:
            if self.client is not None:
                self.client.futures_ping()
                return {"status": "healthy", "type": "binance_futures"}
            else:
                return {
                    "status": "degraded",
                    "type": "binance_futures",
                    "error": "Client not initialized",
                }
        except Exception as e:
            logger.error(f"Binance Futures health check error: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def _load_exchange_info(self) -> None:
        """Load futures exchange information and symbol details"""
        try:
            # Get futures exchange information
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            self.exchange_info = self.client.futures_exchange_info()

            # Create symbol info lookup
            for symbol_data in self.exchange_info["symbols"]:
                symbol = symbol_data["symbol"]
                self.symbol_info[symbol] = {
                    "baseAsset": symbol_data["baseAsset"],
                    "quoteAsset": symbol_data["quoteAsset"],
                    "status": symbol_data["status"],
                    "filters": symbol_data["filters"],
                }

            logger.info(
                f"Loaded futures exchange info for {len(self.symbol_info)} symbols"
            )

        except Exception as e:
            logger.error(f"Failed to load futures exchange info: {e}")
            raise

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a trade order on Binance Futures"""
        if not self.initialized:
            await self.initialize()

        try:
            logger.info(
                f"Executing {order.type} {order.side} order for "
                f"{order.amount} {order.symbol} "
                f"(reduce_only={order.reduce_only})"
            )

            # Validate order
            await self._validate_order(order)

            # Execute based on order type
            if order.type == "market":
                result = await self._execute_market_order(order)
            elif order.type == "limit":
                result = await self._execute_limit_order(order)
            elif order.type == "stop":
                result = await self._execute_stop_order(order)
            elif order.type == "stop_limit":
                result = await self._execute_stop_limit_order(order)
            elif order.type == "take_profit":
                result = await self._execute_take_profit_order(order)
            elif order.type == "take_profit_limit":
                result = await self._execute_take_profit_limit_order(order)
            else:
                raise ValueError(f"Unsupported order type: {order.type}")

            # Format and return result
            return self._format_execution_result(result, order)

        except BinanceAPIException as e:
            logger.error(f"Binance Futures API error: {e}")
            return self._format_error_result(str(e), order)
        except Exception as e:
            logger.error(f"Order execution error: {e}")
            return self._format_error_result(str(e), order)

    async def _get_current_price(self, symbol: str) -> float:
        """Get current market price for a symbol"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        ticker = self.client.futures_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    async def _validate_notional(self, order: TradeOrder, price: float) -> None:
        """Validate order meets minimum notional value requirement"""
        # Reduce-only orders are exempt from MIN_NOTIONAL
        if order.reduce_only:
            logger.debug(
                f"Skipping notional validation for reduce_only order: {order.symbol}"
            )
            return

        # Get minimum notional for symbol
        min_info = self.get_min_order_amount(order.symbol)
        min_notional = float(min_info["min_notional"])

        # Calculate order notional value
        notional_value = price * order.amount

        # Calculate minimum quantity needed
        min_qty_needed = min_notional / price

        # Log detailed validation info
        logger.info(
            f"Notional validation for {order.symbol}: "
            f"Order=${notional_value:.2f} (qty={order.amount:.6f} × ${price:.2f}), "
            f"Required=${min_notional:.2f} (min_qty={min_qty_needed:.6f})"
        )

        # Validate
        if notional_value < min_notional:
            raise ValueError(
                f"Order notional ${notional_value:.2f} is below minimum ${min_notional:.2f} "
                f"for {order.symbol}. Need quantity >= {min_qty_needed:.6f} at ${price:.2f}. "
                f"Current quantity: {order.amount:.6f}. Use reduce_only flag if closing position."
            )

        logger.info(f"✓ Notional validation passed for {order.symbol}")

    async def _validate_order(self, order: TradeOrder) -> None:
        """Validate order parameters"""
        # Check if symbol is supported
        if order.symbol not in self.symbol_info:
            raise ValueError(f"Symbol {order.symbol} not supported")

        symbol_data = self.symbol_info[order.symbol]
        if symbol_data["status"] != "TRADING":
            raise ValueError(f"Symbol {order.symbol} is not trading")

        # Validate order type
        if order.type not in [
            "market",
            "limit",
            "stop",
            "stop_limit",
            "take_profit",
            "take_profit_limit",
        ]:
            raise ValueError(f"Invalid order type: {order.type}")

        # Validate side
        if order.side not in ["buy", "sell"]:
            raise ValueError(f"Invalid order side: {order.side}")

        # Validate amount
        if order.amount <= 0:
            raise ValueError("Order amount must be positive")

        # Validate price for limit orders
        if order.type in ["limit", "stop_limit", "take_profit_limit"]:
            if order.target_price is None:
                raise ValueError("Target price required for limit orders")

        # Validate stop price for stop orders
        if order.type in ["stop", "stop_limit"]:
            if order.stop_loss is None:
                raise ValueError("Stop loss price required for stop orders")

        # Validate minimum notional value
        # For limit orders, use target_price
        if order.type in ["limit", "stop_limit", "take_profit_limit"]:
            if order.target_price is None:
                raise ValueError("Target price required for limit orders")
            await self._validate_notional(order, order.target_price)
        # For market orders, fetch current price
        elif order.type in ["market", "stop", "take_profit"]:
            current_price = await self._get_current_price(order.symbol)
            await self._validate_notional(order, current_price)

    async def _execute_market_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a market order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        # Add quote order quantity for market orders if specified
        if hasattr(order, "quote_quantity") and order.quote_quantity:
            params["quoteOrderQty"] = order.quote_quantity

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for market order"
            )
        return result

    async def _execute_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a limit order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        if order.target_price is None:
            raise ValueError("Target price required for limit orders")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_LIMIT,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for limit order"
            )
        return result

    async def _execute_stop_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a stop market order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        if order.stop_loss is None:
            raise ValueError("Stop loss price required for stop orders")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_STOP_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "stopPrice": self._format_price(order.symbol, order.stop_loss),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for stop order"
            )
        return result

    async def _execute_stop_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a stop limit order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        if order.target_price is None:
            raise ValueError("Target price required for stop limit orders")
        if order.stop_loss is None:
            raise ValueError("Stop loss price required for stop limit orders")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_STOP,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price),
            "stopPrice": self._format_price(order.symbol, order.stop_loss),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for stop limit order"
            )
        return result

    async def _execute_take_profit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a take profit market order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        if order.take_profit is None:
            raise ValueError("Take profit price required for take profit orders")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "stopPrice": self._format_price(order.symbol, order.take_profit),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for take profit order"
            )
        return result

    async def _execute_take_profit_limit_order(
        self, order: TradeOrder
    ) -> dict[str, Any]:
        """Execute a take profit limit order"""
        if self.client is None:
            raise RuntimeError("Binance Futures client not initialized")
        if order.target_price is None:
            raise ValueError("Target price required for take profit limit orders")
        if order.take_profit is None:
            raise ValueError("Take profit price required for take profit limit orders")
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_TAKE_PROFIT,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price),
            "stopPrice": self._format_price(order.symbol, order.take_profit),
            "reduceOnly": order.reduce_only,
        }

        # Add positionSide for hedge mode
        if order.position_side:
            params["positionSide"] = order.position_side

        result = await self._execute_with_retry(
            self.client.futures_create_order, **params
        )
        if not isinstance(result, dict):
            raise RuntimeError(
                "Binance Futures API did not return a dict for take profit limit order"
            )
        return result

    async def _execute_with_retry(self, func: Any, **kwargs: Any) -> Any:
        """Execute function with retry logic"""
        last_exception: Exception | None = None

        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                return func(**kwargs)  # Futures client is synchronous
            except BinanceAPIException as e:
                # Don't retry on certain errors
                if e.code in [
                    -2010,  # Insufficient balance
                    -2011,  # Invalid symbol
                    -2013,  # Invalid order type
                    -2014,  # Invalid price
                    -2015,  # Invalid quantity
                    -4164,  # MIN_NOTIONAL validation error
                ]:
                    raise
                last_exception = e
            except Exception as e:
                last_exception = e

            if attempt < MAX_RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
                logger.warning(
                    f"Retry {attempt + 1}/{MAX_RETRY_ATTEMPTS} after "
                    f"{wait_time}s: {last_exception}"
                )
                await asyncio.sleep(wait_time)

        if last_exception is None:
            raise RuntimeError("No exception captured during retry")
        raise last_exception

    def get_min_order_amount(self, symbol: str) -> dict[str, Any]:
        """Get minimum order amount for a symbol based on Binance filters"""
        if symbol not in self.symbol_info:
            raise ValueError(f"Symbol {symbol} not found in exchange info")

        symbol_data = self.symbol_info[symbol]
        filters = symbol_data["filters"]

        # Find LOT_SIZE filter for minimum quantity
        lot_size_filter = next(
            (f for f in filters if f["filterType"] == "LOT_SIZE"), None
        )

        # Find MIN_NOTIONAL filter for minimum order value
        min_notional_filter = next(
            (f for f in filters if f["filterType"] == "MIN_NOTIONAL"), None
        )

        # Find PRICE_FILTER for price precision (unused for now)
        # price_filter = next(
        #     (f for f in filters if f["filterType"] == "PRICE_FILTER"), None
        # )

        min_qty = float(lot_size_filter["minQty"]) if lot_size_filter else 0.001
        min_notional = (
            float(min_notional_filter["notional"]) if min_notional_filter else 20.0
        )
        step_size = float(lot_size_filter["stepSize"]) if lot_size_filter else 0.001

        # Calculate precision based on step size
        precision = (
            len(str(step_size).split(".")[-1].rstrip("0"))
            if "." in str(step_size)
            else 0
        )

        return {
            "symbol": symbol,
            "min_qty": min_qty,
            "min_notional": min_notional,
            "step_size": step_size,
            "precision": precision,
            "base_asset": symbol_data["baseAsset"],
            "quote_asset": symbol_data["quoteAsset"],
        }

    def calculate_min_order_amount(
        self, symbol: str, current_price: float | None = None
    ) -> float:
        """Calculate the minimum order amount that meets all requirements"""
        try:
            min_info = self.get_min_order_amount(symbol)
            min_qty = float(min_info["min_qty"])
            min_notional = float(min_info["min_notional"])

            # If no current price provided, use min_qty as fallback
            if current_price is None:
                return min_qty

            # Calculate minimum quantity based on notional value
            min_qty_by_notional = min_notional / current_price

            # Use the larger of the two minimums
            final_min_qty = max(min_qty, min_qty_by_notional)

            # Add 5% safety margin to avoid rounding errors
            final_min_qty = final_min_qty * 1.05

            # Round to the appropriate precision
            precision = min_info["precision"]
            final_min_qty = round(final_min_qty, precision)

            # Log the calculation
            logger.debug(
                f"Calculated min order amount for {symbol}: "
                f"{final_min_qty} (price: ${current_price:.2f}, "
                f"min_notional: ${min_notional:.2f})"
            )

            return final_min_qty

        except Exception as e:
            logger.warning(f"Error calculating min order amount for {symbol}: {e}")
            return 0.001  # Fallback to safe default

    async def get_symbol_min_notional(self, symbol: str) -> dict[str, Any]:
        """
        Get MIN_NOTIONAL and calculate minimum quantity at current price.

        Returns dict with:
        - min_notional: Minimum notional value
        - current_price: Current mark price
        - min_quantity: Minimum quantity needed at current price
        - notional_value: Actual notional value with min_quantity
        """
        if not self.initialized:
            await self.initialize()

        min_info = self.get_min_order_amount(symbol)
        current_price = await self._get_current_price(symbol)
        min_quantity = self.calculate_min_order_amount(symbol, current_price)

        result = {
            "symbol": symbol,
            "min_notional": min_info["min_notional"],
            "current_price": current_price,
            "min_quantity": min_quantity,
            "notional_value": min_quantity * current_price,
        }

        logger.info(
            f"MIN_NOTIONAL info for {symbol}: "
            f"${result['min_notional']:.2f} = "
            f"{result['min_quantity']:.6f} qty × ${result['current_price']:.2f}"
        )

        return result

    def _format_quantity(self, symbol: str, quantity: float) -> str:
        """Format quantity according to symbol precision"""
        if symbol not in self.symbol_info:
            return str(quantity)

        # Find LOT_SIZE filter
        lot_size_filter = next(
            (
                f
                for f in self.symbol_info[symbol]["filters"]
                if f["filterType"] == "LOT_SIZE"
            ),
            None,
        )

        if lot_size_filter:
            step_size = float(lot_size_filter["stepSize"])
            precision = len(str(step_size).split(".")[-1].rstrip("0"))
            return f"{quantity:.{precision}f}"

        return str(quantity)

    def _format_price(self, symbol: str, price: float) -> str:
        """Format price according to symbol precision"""
        if symbol not in self.symbol_info:
            return str(price)

        # Find PRICE_FILTER
        price_filter = next(
            (
                f
                for f in self.symbol_info[symbol]["filters"]
                if f["filterType"] == "PRICE_FILTER"
            ),
            None,
        )

        if price_filter:
            tick_size = float(price_filter["tickSize"])
            precision = len(str(tick_size).split(".")[-1].rstrip("0"))
            return f"{price:.{precision}f}"

        return str(price)

    def _format_execution_result(
        self, result: dict[str, Any], order: TradeOrder
    ) -> dict[str, Any]:
        """Format the execution result"""
        fills = result.get("fills", [])
        total_quote_qty = sum(float(fill["quoteQty"]) for fill in fills)
        total_qty = sum(float(fill["qty"]) for fill in fills)

        return {
            "order_id": result.get("orderId"),
            "status": result.get("status"),
            "side": result.get("side"),
            "type": result.get("type"),
            "amount": total_qty,
            "fill_price": result.get("price"),
            "total_value": total_quote_qty,
            "fees": self._calculate_fees(fills),
            "timestamp": result.get("transactTime"),
            "simulated": False,
            "fills": fills,
            "original_order": order.model_dump(),
        }

    def _format_error_result(self, error: str, order: TradeOrder) -> dict[str, Any]:
        """Format error result"""
        return {
            "order_id": None,
            "status": "failed",
            "error": error,
            "timestamp": int(time.time() * 1000),
            "simulated": False,
            "original_order": order.model_dump(),
        }

    def _calculate_fees(self, fills: list[dict[str, Any]]) -> float:
        """Calculate total fees from fills"""
        total_fees = 0.0
        for fill in fills:
            if "commission" in fill:
                total_fees += float(fill["commission"])
        return total_fees

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information"""
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            account_info = self.client.futures_account()
            return {
                "maker_commission": account_info.get("makerCommission"),
                "taker_commission": account_info.get("takerCommission"),
                "buyer_commission": account_info.get("buyerCommission"),
                "seller_commission": account_info.get("sellerCommission"),
                "can_trade": account_info.get("canTrade"),
                "can_withdraw": account_info.get("canWithdraw"),
                "can_deposit": account_info.get("canDeposit"),
                "assets": account_info.get("assets", []),
            }
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            raise

    async def get_symbol_price(self, symbol: str) -> float:
        """Get current symbol price"""
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            raise

    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol"""
        return await self.get_symbol_price(symbol)

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an existing order"""
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            result = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return {
                "order_id": result.get("orderId"),
                "status": result.get("status"),
                "symbol": result.get("symbol"),
                "timestamp": int(time.time() * 1000),
            }
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise

    async def get_order_status(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Get order status"""
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            order = self.client.futures_get_order(symbol=symbol, orderId=order_id)
            return {
                "order_id": order.get("orderId"),
                "status": order.get("status"),
                "side": order.get("side"),
                "type": order.get("type"),
                "quantity": order.get("origQty"),
                "price": order.get("price"),
                "executed_qty": order.get("executedQty"),
                "cummulative_quote_qty": order.get("cummulativeQuoteQty"),
                "time": order.get("time"),
                "update_time": order.get("updateTime"),
            }
        except Exception as e:
            logger.error(f"Failed to get order status for {order_id}: {e}")
            raise

    async def get_position_info(self) -> list[dict[str, Any]]:
        """Get position information"""
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")
            positions = self.client.futures_position_information()
            # Type cast to satisfy mypy
            return list(positions) if positions else []
        except Exception as e:
            logger.error(f"Failed to get position info: {e}")
            raise

    async def verify_hedge_mode(self) -> dict[str, Any]:
        """Verify if hedge mode is enabled on Binance Futures account

        Returns:
            dict with hedge_mode_enabled flag and position mode status
        """
        if not self.initialized:
            await self.initialize()

        try:
            if self.client is None:
                raise RuntimeError("Binance Futures client not initialized")

            # Get position mode/dual side position setting
            position_mode = self.client.futures_get_position_mode()

            hedge_mode_enabled = position_mode.get("dualSidePosition", False)

            return {
                "hedge_mode_enabled": hedge_mode_enabled,
                "position_mode": "hedge" if hedge_mode_enabled else "one-way",
                "dual_side_position": hedge_mode_enabled,
                "raw_response": position_mode,
            }
        except Exception as e:
            logger.error(f"Failed to verify hedge mode: {e}")
            return {
                "hedge_mode_enabled": False,
                "position_mode": "unknown",
                "error": str(e),
            }

    async def close(self) -> None:
        """Close the Binance Futures client"""
        # UMFutures client doesn't have a close method like AsyncClient
        logger.info("Binance Futures client connection closed")


# Global Binance Futures exchange instance
binance_futures_exchange = BinanceFuturesExchange()
