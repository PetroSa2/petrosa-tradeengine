"""
Binance Exchange Client - Petrosa Trading Engine

This module provides a comprehensive interface to Binance API for executing
all types of trading orders including market, limit, stop, and take-profit orders.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from decimal import Decimal, ROUND_DOWN
from binance import AsyncClient, BinanceAPIException
from binance.enums import *
from contracts.order import TradeOrder
from shared.constants import (
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET,
    BINANCE_TIMEOUT, BINANCE_RETRY_ATTEMPTS, RETRY_DELAY,
    RETRY_BACKOFF_MULTIPLIER, MAX_RETRY_ATTEMPTS
)

logger = logging.getLogger(__name__)


class BinanceExchange:
    """Binance exchange client for executing trades"""

    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self.exchange_info: Dict[str, Any] = {}
        self.symbol_info: Dict[str, Any] = {}
        self.initialized = False

    async def initialize(self):
        """Initialize Binance client and load exchange information"""
        if self.initialized:
            return

        try:
            logger.info("Initializing Binance exchange client...")
            
            # Create async client
            self.client = await AsyncClient.create(
                api_key=BINANCE_API_KEY,
                api_secret=BINANCE_API_SECRET,
                testnet=BINANCE_TESTNET,
                requests_params={'timeout': BINANCE_TIMEOUT}
            )

            # Load exchange information
            await self._load_exchange_info()
            
            self.initialized = True
            logger.info("Binance exchange client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Binance client: {e}")
            raise

    async def _load_exchange_info(self):
        """Load exchange information and symbol details"""
        try:
            # Get exchange information
            self.exchange_info = await self.client.get_exchange_info()
            
            # Create symbol info lookup
            for symbol_data in self.exchange_info['symbols']:
                symbol = symbol_data['symbol']
                self.symbol_info[symbol] = {
                    'baseAsset': symbol_data['baseAsset'],
                    'quoteAsset': symbol_data['quoteAsset'],
                    'status': symbol_data['status'],
                    'filters': symbol_data['filters']
                }
            
            logger.info(f"Loaded exchange info for {len(self.symbol_info)} symbols")
            
        except Exception as e:
            logger.error(f"Failed to load exchange info: {e}")
            raise

    async def execute(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a trade order on Binance"""
        if not self.initialized:
            await self.initialize()

        try:
            logger.info(f"Executing {order.type} {order.side} order for {order.amount} {order.symbol}")

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
            logger.error(f"Binance API error: {e}")
            return self._format_error_result(str(e), order)
        except Exception as e:
            logger.error(f"Order execution error: {e}")
            return self._format_error_result(str(e), order)

    async def _validate_order(self, order: TradeOrder):
        """Validate order parameters"""
        # Check if symbol is supported
        if order.symbol not in self.symbol_info:
            raise ValueError(f"Symbol {order.symbol} not supported")

        symbol_data = self.symbol_info[order.symbol]
        if symbol_data['status'] != 'TRADING':
            raise ValueError(f"Symbol {order.symbol} is not trading")

        # Validate order type
        if order.type not in ["market", "limit", "stop", "stop_limit", "take_profit", "take_profit_limit"]:
            raise ValueError(f"Invalid order type: {order.type}")

        # Validate side
        if order.side not in ["buy", "sell"]:
            raise ValueError(f"Invalid order side: {order.side}")

        # Validate amount
        if order.amount <= 0:
            raise ValueError("Order amount must be positive")

        # Validate price for limit orders
        if order.type in ["limit", "stop_limit", "take_profit_limit"]:
            if not order.target_price or order.target_price <= 0:
                raise ValueError("Target price required for limit orders")

        # Validate stop price for stop orders
        if order.type in ["stop", "stop_limit"]:
            if not order.stop_loss or order.stop_loss <= 0:
                raise ValueError("Stop loss price required for stop orders")

    async def _execute_market_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a market order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount)
        }

        # Add quote order quantity for market orders if specified
        if hasattr(order, 'quote_quantity') and order.quote_quantity:
            params["quoteOrderQty"] = order.quote_quantity

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_limit_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a limit order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_LIMIT,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price)
        }

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_stop_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a stop market order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_STOP_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "stopPrice": self._format_price(order.symbol, order.stop_loss)
        }

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_stop_limit_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a stop limit order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_STOP_LOSS_LIMIT,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price),
            "stopPrice": self._format_price(order.symbol, order.stop_loss)
        }

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_take_profit_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a take profit market order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_TAKE_PROFIT_MARKET,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "stopPrice": self._format_price(order.symbol, order.take_profit)
        }

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_take_profit_limit_order(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a take profit limit order"""
        params = {
            "symbol": order.symbol,
            "side": SIDE_BUY if order.side == "buy" else SIDE_SELL,
            "type": ORDER_TYPE_TAKE_PROFIT_LIMIT,
            "timeInForce": order.time_in_force or TIME_IN_FORCE_GTC,
            "quantity": self._format_quantity(order.symbol, order.amount),
            "price": self._format_price(order.symbol, order.target_price),
            "stopPrice": self._format_price(order.symbol, order.take_profit)
        }

        return await self._execute_with_retry(
            self.client.create_order,
            **params
        )

    async def _execute_with_retry(self, func, **kwargs):
        """Execute function with retry logic"""
        last_exception = None
        
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                return await func(**kwargs)
            except BinanceAPIException as e:
                # Don't retry on certain errors
                if e.code in [-2010, -2011, -2013, -2014, -2015]:  # Insufficient balance, invalid symbol, etc.
                    raise
                last_exception = e
            except Exception as e:
                last_exception = e
            
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER ** attempt)
                logger.warning(f"Retry {attempt + 1}/{MAX_RETRY_ATTEMPTS} after {wait_time}s: {last_exception}")
                await asyncio.sleep(wait_time)
        
        raise last_exception

    def _format_quantity(self, symbol: str, quantity: float) -> str:
        """Format quantity according to symbol precision"""
        if symbol not in self.symbol_info:
            return str(quantity)
        
        # Find LOT_SIZE filter
        lot_size_filter = next(
            (f for f in self.symbol_info[symbol]['filters'] if f['filterType'] == 'LOT_SIZE'),
            None
        )
        
        if lot_size_filter:
            step_size = float(lot_size_filter['stepSize'])
            precision = len(str(step_size).split('.')[-1].rstrip('0'))
            return f"{quantity:.{precision}f}"
        
        return str(quantity)

    def _format_price(self, symbol: str, price: float) -> str:
        """Format price according to symbol precision"""
        if symbol not in self.symbol_info:
            return str(price)
        
        # Find PRICE_FILTER
        price_filter = next(
            (f for f in self.symbol_info[symbol]['filters'] if f['filterType'] == 'PRICE_FILTER'),
            None
        )
        
        if price_filter:
            tick_size = float(price_filter['tickSize'])
            precision = len(str(tick_size).split('.')[-1].rstrip('0'))
            return f"{price:.{precision}f}"
        
        return str(price)

    def _format_execution_result(self, result: Dict[str, Any], order: TradeOrder) -> Dict[str, Any]:
        """Format the execution result"""
        fills = result.get('fills', [])
        total_quote_qty = sum(float(fill['quoteQty']) for fill in fills)
        total_qty = sum(float(fill['qty']) for fill in fills)
        
        return {
            "order_id": result.get('orderId'),
            "status": result.get('status'),
            "side": result.get('side'),
            "type": result.get('type'),
            "amount": total_qty,
            "fill_price": result.get('price'),
            "total_value": total_quote_qty,
            "fees": self._calculate_fees(fills),
            "timestamp": result.get('transactTime'),
            "simulated": False,
            "fills": fills,
            "original_order": order.model_dump()
        }

    def _format_error_result(self, error: str, order: TradeOrder) -> Dict[str, Any]:
        """Format error result"""
        return {
            "order_id": None,
            "status": "failed",
            "error": error,
            "timestamp": int(time.time() * 1000),
            "simulated": False,
            "original_order": order.model_dump()
        }

    def _calculate_fees(self, fills: List[Dict[str, Any]]) -> float:
        """Calculate total fees from fills"""
        total_fees = 0.0
        for fill in fills:
            if 'commission' in fill:
                total_fees += float(fill['commission'])
        return total_fees

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        if not self.initialized:
            await self.initialize()
        
        try:
            account_info = await self.client.get_account()
            return {
                "maker_commission": account_info.get('makerCommission'),
                "taker_commission": account_info.get('takerCommission'),
                "buyer_commission": account_info.get('buyerCommission'),
                "seller_commission": account_info.get('sellerCommission'),
                "can_trade": account_info.get('canTrade'),
                "can_withdraw": account_info.get('canWithdraw'),
                "can_deposit": account_info.get('canDeposit'),
                "balances": account_info.get('balances', [])
            }
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            raise

    async def get_symbol_price(self, symbol: str) -> float:
        """Get current symbol price"""
        if not self.initialized:
            await self.initialize()
        
        try:
            ticker = await self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            raise

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an existing order"""
        if not self.initialized:
            await self.initialize()
        
        try:
            result = await self.client.cancel_order(symbol=symbol, orderId=order_id)
            return {
                "order_id": result.get('orderId'),
                "status": result.get('status'),
                "symbol": result.get('symbol'),
                "timestamp": int(time.time() * 1000)
            }
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise

    async def get_order_status(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Get order status"""
        if not self.initialized:
            await self.initialize()
        
        try:
            order = await self.client.get_order(symbol=symbol, orderId=order_id)
            return {
                "order_id": order.get('orderId'),
                "status": order.get('status'),
                "side": order.get('side'),
                "type": order.get('type'),
                "quantity": order.get('origQty'),
                "price": order.get('price'),
                "executed_qty": order.get('executedQty'),
                "cummulative_quote_qty": order.get('cummulativeQuoteQty'),
                "time": order.get('time'),
                "update_time": order.get('updateTime')
            }
        except Exception as e:
            logger.error(f"Failed to get order status for {order_id}: {e}")
            raise

    async def close(self):
        """Close the Binance client"""
        if self.client:
            await self.client.close_connection()
            logger.info("Binance client connection closed")


# Global Binance exchange instance
binance_exchange = BinanceExchange() 