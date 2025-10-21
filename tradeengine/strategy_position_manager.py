"""
Strategy Position Manager - Manages virtual strategy positions

This module separates strategy positions (virtual) from exchange positions (physical).
Each signal creates a strategy position with its own TP/SL that can close independently
of the exchange position.

Key Concepts:
- Strategy Position: Virtual position with strategy's own TP/SL
- Exchange Position: Actual aggregated position on Binance
- Position Contribution: Links strategy position to exchange position

This enables:
- Per-strategy TP/SL tracking
- Strategy-level analytics (which strategies hit TP vs SL)
- Profit attribution to contributing strategies
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from contracts.order import TradeOrder
from contracts.signal import Signal

logger = logging.getLogger(__name__)

# Import MySQL client
try:
    from shared.mysql_client import mysql_client
except ImportError:
    mysql_client = None  # type: ignore
    logger.warning("MySQL client not available - strategy position tracking disabled")


class StrategyPositionManager:
    """Manages virtual strategy positions and their contributions to exchange positions"""

    def __init__(self) -> None:
        self.strategy_positions: dict[str, dict[str, Any]] = (
            {}
        )  # strategy_position_id -> position
        self.exchange_positions: dict[str, dict[str, Any]] = (
            {}
        )  # exchange_position_key -> position
        self.contributions: dict[str, list[dict[str, Any]]] = (
            {}
        )  # exchange_position_key -> contributions

    async def initialize(self) -> None:
        """Initialize strategy position manager"""
        try:
            # Connect MySQL if available
            if mysql_client:
                await mysql_client.connect()
                logger.info("Strategy position manager initialized with MySQL")
            else:
                logger.warning("Strategy position manager running without MySQL")
        except Exception as e:
            logger.error(f"Failed to initialize strategy position manager: {e}")

    async def create_strategy_position(
        self, signal: Signal, order: TradeOrder, execution_result: dict[str, Any]
    ) -> str:
        """Create a new strategy position when signal is executed

        Args:
            signal: The original signal
            order: The executed order
            execution_result: Result from exchange execution

        Returns:
            strategy_position_id: UUID for the strategy position
        """
        try:
            # Generate strategy position ID
            strategy_position_id = str(uuid.uuid4())

            # Determine position side
            position_side = "LONG" if signal.action == "buy" else "SHORT"

            # Extract execution details
            entry_price = float(
                execution_result.get("fill_price", signal.current_price)
            )
            entry_quantity = float(execution_result.get("amount", signal.quantity))
            entry_order_id = execution_result.get("order_id")

            # Calculate TP/SL prices
            take_profit_price = None
            stop_loss_price = None

            if signal.take_profit_pct:
                if position_side == "LONG":
                    take_profit_price = entry_price * (1 + signal.take_profit_pct)
                else:
                    take_profit_price = entry_price * (1 - signal.take_profit_pct)

            if signal.stop_loss_pct:
                if position_side == "LONG":
                    stop_loss_price = entry_price * (1 - signal.stop_loss_pct)
                else:
                    stop_loss_price = entry_price * (1 + signal.stop_loss_pct)

            # Exchange position key
            exchange_position_key = f"{signal.symbol}_{position_side}"

            # Create strategy position record
            strategy_position = {
                "strategy_position_id": strategy_position_id,
                "strategy_id": signal.strategy_id,
                "signal_id": signal.signal_id or signal.id,
                "symbol": signal.symbol,
                "side": position_side,
                "entry_quantity": entry_quantity,
                "entry_price": entry_price,
                "entry_time": datetime.utcnow(),
                "entry_order_id": entry_order_id,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "tp_order_id": order.take_profit,  # Will be set when OCO placed
                "sl_order_id": order.stop_loss,  # Will be set when OCO placed
                "status": "open",
                "exchange_position_key": exchange_position_key,
                "strategy_metadata": {
                    "timeframe": signal.timeframe,
                    "confidence": signal.confidence,
                    "strength": signal.strength.value if signal.strength else None,
                    "rationale": signal.rationale,
                },
            }

            # Store in memory
            self.strategy_positions[strategy_position_id] = strategy_position

            # Persist to MySQL
            if mysql_client:
                await self._persist_strategy_position(strategy_position)

            # Update exchange position
            await self._update_exchange_position(
                exchange_position_key,
                signal.symbol,
                position_side,
                entry_quantity,
                entry_price,
                signal.strategy_id,
            )

            # Create contribution record
            await self._create_contribution(
                strategy_position_id,
                exchange_position_key,
                signal.strategy_id,
                signal.symbol,
                position_side,
                entry_quantity,
                entry_price,
            )

            logger.info(
                f"Created strategy position {strategy_position_id} for {signal.strategy_id}: "
                f"{signal.symbol} {position_side} {entry_quantity} @ {entry_price}"
            )

            return strategy_position_id

        except Exception as e:
            logger.error(f"Error creating strategy position: {e}")
            raise

    async def close_strategy_position(
        self,
        strategy_position_id: str,
        exit_price: float,
        exit_quantity: float | None = None,
        close_reason: str = "manual",
        exit_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Close a strategy position when TP/SL triggers

        Args:
            strategy_position_id: Strategy position to close
            exit_price: Exit price
            exit_quantity: Exit quantity (None = close full position)
            close_reason: Reason for closure (take_profit, stop_loss, manual)
            exit_order_id: Order ID that triggered the close

        Returns:
            Closure details with PnL
        """
        try:
            position = self.strategy_positions.get(strategy_position_id)
            if not position:
                logger.warning(f"Strategy position {strategy_position_id} not found")
                return {}

            # Calculate exit quantity
            if exit_quantity is None:
                exit_quantity = position["entry_quantity"]

            # Calculate PnL
            entry_price = position["entry_price"]
            entry_quantity = position["entry_quantity"]

            if position["side"] == "LONG":
                pnl = (exit_price - entry_price) * exit_quantity
            else:  # SHORT
                pnl = (entry_price - exit_price) * exit_quantity

            pnl_pct = (
                (pnl / (entry_price * exit_quantity)) * 100 if entry_price > 0 else 0
            )

            # Update position
            position["status"] = (
                "closed" if exit_quantity >= entry_quantity else "partial"
            )
            position["exit_quantity"] = exit_quantity
            position["exit_price"] = exit_price
            position["exit_time"] = datetime.utcnow()
            position["exit_order_id"] = exit_order_id
            position["close_reason"] = close_reason
            position["realized_pnl"] = pnl
            position["realized_pnl_pct"] = pnl_pct

            # Persist to MySQL
            if mysql_client:
                await self._update_strategy_position_closure(
                    strategy_position_id, position
                )

            # Update contribution
            await self._close_contribution(
                strategy_position_id, exit_price, pnl, pnl_pct, close_reason
            )

            # Update exchange position
            await self._reduce_exchange_position(
                position["exchange_position_key"], exit_quantity, exit_price
            )

            logger.info(
                f"Closed strategy position {strategy_position_id}: "
                f"{close_reason} at {exit_price}, PnL: ${pnl:.2f} ({pnl_pct:.2f}%)"
            )

            return {
                "strategy_position_id": strategy_position_id,
                "strategy_id": position["strategy_id"],
                "symbol": position["symbol"],
                "side": position["side"],
                "close_reason": close_reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": exit_quantity,
                "realized_pnl": pnl,
                "realized_pnl_pct": pnl_pct,
            }

        except Exception as e:
            logger.error(f"Error closing strategy position: {e}")
            raise

    async def _persist_strategy_position(self, position: dict[str, Any]) -> None:
        """Persist strategy position to MySQL"""
        if not mysql_client:
            return

        try:
            await mysql_client.execute_query(
                """
                INSERT INTO strategy_positions (
                    strategy_position_id, strategy_id, signal_id, symbol, side,
                    entry_quantity, entry_price, entry_time, entry_order_id,
                    take_profit_price, stop_loss_price, tp_order_id, sl_order_id,
                    status, exchange_position_key, strategy_metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    position["strategy_position_id"],
                    position["strategy_id"],
                    position.get("signal_id"),
                    position["symbol"],
                    position["side"],
                    position["entry_quantity"],
                    position["entry_price"],
                    position["entry_time"],
                    position.get("entry_order_id"),
                    position.get("take_profit_price"),
                    position.get("stop_loss_price"),
                    position.get("tp_order_id"),
                    position.get("sl_order_id"),
                    position["status"],
                    position["exchange_position_key"],
                    str(position.get("strategy_metadata", {})),
                ),
                fetch=False,
            )
            logger.debug(
                f"Persisted strategy position {position['strategy_position_id']} to MySQL"
            )
        except Exception as e:
            logger.error(f"Failed to persist strategy position: {e}")

    async def _update_strategy_position_closure(
        self, strategy_position_id: str, position: dict[str, Any]
    ) -> None:
        """Update strategy position closure details in MySQL"""
        if not mysql_client:
            return

        try:
            await mysql_client.execute_query(
                """
                UPDATE strategy_positions SET
                    status = %s,
                    exit_quantity = %s,
                    exit_price = %s,
                    exit_time = %s,
                    exit_order_id = %s,
                    close_reason = %s,
                    realized_pnl = %s,
                    realized_pnl_pct = %s
                WHERE strategy_position_id = %s
                """,
                (
                    position["status"],
                    position.get("exit_quantity"),
                    position.get("exit_price"),
                    position.get("exit_time"),
                    position.get("exit_order_id"),
                    position.get("close_reason"),
                    position.get("realized_pnl"),
                    position.get("realized_pnl_pct"),
                    strategy_position_id,
                ),
                fetch=False,
            )
            logger.debug(
                f"Updated strategy position closure for {strategy_position_id}"
            )
        except Exception as e:
            logger.error(f"Failed to update strategy position closure: {e}")

    async def _update_exchange_position(
        self,
        exchange_position_key: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str,
    ) -> None:
        """Update or create exchange position"""
        try:
            if exchange_position_key not in self.exchange_positions:
                # Create new exchange position
                self.exchange_positions[exchange_position_key] = {
                    "exchange_position_key": exchange_position_key,
                    "symbol": symbol,
                    "side": side,
                    "current_quantity": quantity,
                    "weighted_avg_price": price,
                    "contributing_strategies": [strategy_id],
                    "total_contributions": 1,
                    "first_entry_time": datetime.utcnow(),
                    "last_update_time": datetime.utcnow(),
                    "status": "open",
                }
            else:
                # Update existing position
                position = self.exchange_positions[exchange_position_key]
                old_quantity = position["current_quantity"]
                old_price = position["weighted_avg_price"]

                # Calculate new weighted average price
                new_quantity = old_quantity + quantity
                new_weighted_price = (
                    (old_quantity * old_price + quantity * price) / new_quantity
                    if new_quantity > 0
                    else price
                )

                position["current_quantity"] = new_quantity
                position["weighted_avg_price"] = new_weighted_price
                position["last_update_time"] = datetime.utcnow()
                position["total_contributions"] += 1

                if strategy_id not in position["contributing_strategies"]:
                    position["contributing_strategies"].append(strategy_id)

            # Persist to MySQL
            if mysql_client:
                await self._persist_exchange_position(exchange_position_key)

        except Exception as e:
            logger.error(f"Error updating exchange position: {e}")

    async def _reduce_exchange_position(
        self, exchange_position_key: str, quantity: float, price: float
    ) -> None:
        """Reduce exchange position quantity when strategy position closes"""
        try:
            if exchange_position_key not in self.exchange_positions:
                logger.warning(f"Exchange position {exchange_position_key} not found")
                return

            position = self.exchange_positions[exchange_position_key]
            position["current_quantity"] -= quantity
            position["last_update_time"] = datetime.utcnow()

            if position["current_quantity"] <= 0:
                position["status"] = "closed"
                logger.info(f"Exchange position {exchange_position_key} fully closed")

            # Persist to MySQL
            if mysql_client:
                await self._persist_exchange_position(exchange_position_key)

        except Exception as e:
            logger.error(f"Error reducing exchange position: {e}")

    async def _persist_exchange_position(self, exchange_position_key: str) -> None:
        """Persist exchange position to MySQL"""
        if not mysql_client:
            return

        try:
            position = self.exchange_positions[exchange_position_key]

            await mysql_client.execute_query(
                """
                INSERT INTO exchange_positions (
                    exchange_position_key, symbol, side, current_quantity,
                    weighted_avg_price, first_entry_time, last_update_time,
                    status, contributing_strategies, total_contributions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    current_quantity = VALUES(current_quantity),
                    weighted_avg_price = VALUES(weighted_avg_price),
                    last_update_time = VALUES(last_update_time),
                    status = VALUES(status),
                    contributing_strategies = VALUES(contributing_strategies),
                    total_contributions = VALUES(total_contributions)
                """,
                (
                    exchange_position_key,
                    position["symbol"],
                    position["side"],
                    position["current_quantity"],
                    position["weighted_avg_price"],
                    position.get("first_entry_time"),
                    position["last_update_time"],
                    position["status"],
                    str(position["contributing_strategies"]),
                    position["total_contributions"],
                ),
                fetch=False,
            )
        except Exception as e:
            logger.error(f"Failed to persist exchange position: {e}")

    async def _create_contribution(
        self,
        strategy_position_id: str,
        exchange_position_key: str,
        strategy_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        price: float,
    ) -> None:
        """Create contribution record linking strategy position to exchange position"""
        try:
            contribution_id = str(uuid.uuid4())

            # Get exchange position state
            exchange_pos = self.exchange_positions.get(exchange_position_key)
            qty_before = (
                exchange_pos["current_quantity"] - quantity if exchange_pos else 0
            )
            qty_after = exchange_pos["current_quantity"] if exchange_pos else quantity
            sequence = exchange_pos["total_contributions"] if exchange_pos else 1

            contribution = {
                "contribution_id": contribution_id,
                "strategy_position_id": strategy_position_id,
                "exchange_position_key": exchange_position_key,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "position_side": position_side,
                "contribution_quantity": quantity,
                "contribution_entry_price": price,
                "contribution_time": datetime.utcnow(),
                "position_sequence": sequence,
                "exchange_quantity_before": qty_before,
                "exchange_quantity_after": qty_after,
                "status": "active",
            }

            # Store in memory
            if exchange_position_key not in self.contributions:
                self.contributions[exchange_position_key] = []
            self.contributions[exchange_position_key].append(contribution)

            # Persist to MySQL
            if mysql_client:
                await mysql_client.execute_query(
                    """
                    INSERT INTO position_contributions (
                        contribution_id, strategy_position_id, exchange_position_key,
                        strategy_id, symbol, position_side, contribution_quantity,
                        contribution_entry_price, contribution_time, position_sequence,
                        exchange_quantity_before, exchange_quantity_after, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        contribution_id,
                        strategy_position_id,
                        exchange_position_key,
                        strategy_id,
                        symbol,
                        position_side,
                        quantity,
                        price,
                        contribution["contribution_time"],
                        sequence,
                        qty_before,
                        qty_after,
                        "active",
                    ),
                    fetch=False,
                )

        except Exception as e:
            logger.error(f"Error creating contribution: {e}")

    async def _close_contribution(
        self,
        strategy_position_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        close_reason: str,
    ) -> None:
        """Close contribution record when strategy position closes"""
        if not mysql_client:
            return

        try:
            await mysql_client.execute_query(
                """
                UPDATE position_contributions SET
                    status = 'closed',
                    exit_time = %s,
                    exit_price = %s,
                    contribution_pnl = %s,
                    contribution_pnl_pct = %s,
                    close_reason = %s
                WHERE strategy_position_id = %s
                """,
                (
                    datetime.utcnow(),
                    exit_price,
                    pnl,
                    pnl_pct,
                    close_reason,
                    strategy_position_id,
                ),
                fetch=False,
            )
        except Exception as e:
            logger.error(f"Error closing contribution: {e}")

    def get_strategy_position(self, strategy_position_id: str) -> dict[str, Any] | None:
        """Get strategy position by ID"""
        return self.strategy_positions.get(strategy_position_id)

    def get_strategy_positions_by_strategy(
        self, strategy_id: str
    ) -> list[dict[str, Any]]:
        """Get all strategy positions for a strategy"""
        return [
            pos
            for pos in self.strategy_positions.values()
            if pos["strategy_id"] == strategy_id
        ]

    def get_exchange_position(
        self, exchange_position_key: str
    ) -> dict[str, Any] | None:
        """Get exchange position"""
        return self.exchange_positions.get(exchange_position_key)

    def get_contributions(self, exchange_position_key: str) -> list[dict[str, Any]]:
        """Get all contributions to an exchange position"""
        return self.contributions.get(exchange_position_key, [])


# Global strategy position manager instance
strategy_position_manager = StrategyPositionManager()
