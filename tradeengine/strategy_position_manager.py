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
from shared.constants import TE_EXCHANGE_TRUTH_STORE_ENABLED, UTC

# Import Data Manager position client
from shared.mysql_client import position_client
from shared.retry import PersistResult
from tradeengine.exchange_truth_store import ExchangeTruthStore
from tradeengine.metrics import (
    otel_position_persist_failed,
    position_persist_failed_total,
)
from tradeengine.services.alert_publisher import alert_publisher
from tradeengine.services.persist_retry_queue import PendingWrite, persist_retry_queue

logger = logging.getLogger(__name__)


def _on_persist_failure(result: PersistResult, position_data: dict[str, Any]) -> None:
    """Emit metric + alert + enqueue retry on a failed persist. Never raises."""
    try:
        sym = result.symbol or str(position_data.get("symbol", "unknown"))
        pos_side = str(position_data.get("position_side", "unknown"))
        position_persist_failed_total.labels(
            symbol=sym,
            position_side=pos_side,
            operation=result.operation,
            reason=result.reason or "unknown",
        ).inc()
        otel_position_persist_failed.add(
            1,
            {
                "symbol": sym,
                "position_side": pos_side,
                "operation": result.operation,
                "reason": result.reason or "unknown",
            },
        )
    except Exception as exc:
        logger.warning("Metric emission failed for persist_failed: %s", exc)

    try:
        import asyncio

        loop = asyncio.get_event_loop()
        sym = result.symbol or str(position_data.get("symbol", "unknown"))
        loop.create_task(
            alert_publisher.publish(
                alert_name=f"persist_failed.{sym}",
                severity="critical",
                payload={
                    "symbol": sym,
                    "operation": result.operation,
                    "position_id": result.position_id,
                    "error": result.error,
                    "reason": result.reason,
                },
            )
        )
    except Exception as exc:
        logger.warning("Alert publish failed for persist_failed: %s", exc)

    try:
        pw = PendingWrite(
            operation=result.operation,
            data=dict(position_data),
            symbol=result.symbol or str(position_data.get("symbol", "")),
            position_id=result.position_id,
            last_error=result.error,
        )
        persist_retry_queue.enqueue(pw)
    except Exception as exc:
        logger.warning("Enqueue to persist_retry_queue failed: %s", exc)


class StrategyPositionManager:
    """Manages virtual strategy positions and their contributions to exchange positions"""

    def __init__(self) -> None:
        self.strategy_positions: dict[
            str, dict[str, Any]
        ] = {}  # strategy_position_id -> position
        self.exchange_positions: dict[
            str, dict[str, Any]
        ] = {}  # exchange_position_key -> position
        self.contributions: dict[
            str, list[dict[str, Any]]
        ] = {}  # exchange_position_key -> contributions
        # AC4 (#459 — 446-C): injected by Dispatcher after UserDataStreamConsumer starts.
        self.exchange_truth_store: ExchangeTruthStore | None = None

    async def initialize(self) -> None:
        """Initialize strategy position manager"""
        try:
            # Connect to Data Manager
            try:
                await position_client.connect()
                logger.info("Strategy position manager initialized with Data Manager")
            except Exception as data_manager_error:
                logger.warning(
                    f"Data Manager not available - strategy position tracking disabled: {data_manager_error}"
                )
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

            # CRITICAL FIX: Check for absolute price values first, then percentages
            # Signals from TA Bot send absolute prices (stop_loss, take_profit)
            # Some signals may still use percentages (stop_loss_pct, take_profit_pct)

            if signal.take_profit:
                # Use absolute take profit price from signal
                take_profit_price = float(signal.take_profit)
            elif signal.take_profit_pct:
                # Calculate from percentage
                if position_side == "LONG":
                    take_profit_price = entry_price * (1 + signal.take_profit_pct)
                else:
                    take_profit_price = entry_price * (1 - signal.take_profit_pct)

            if signal.stop_loss:
                # Use absolute stop loss price from signal
                stop_loss_price = float(signal.stop_loss)
            elif signal.stop_loss_pct:
                # Calculate from percentage
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
                "entry_time": datetime.now(UTC),
                "entry_order_id": entry_order_id,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                # AC3 of #424: these must hold real Binance algo-order IDs,
                # not the price-shaped placeholders that surfaced in the
                # 2026-05-30 incident. They are populated by the OCO-success
                # path via set_strategy_position_orders() once the algo orders
                # come back with their real IDs.
                "tp_order_id": None,
                "sl_order_id": None,
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

            # Persist to Data Manager
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

    async def set_strategy_position_orders(
        self,
        strategy_position_id: str,
        sl_order_id: str | None = None,
        tp_order_id: str | None = None,
    ) -> None:
        # AC3 of #424 (2026-05-30 OCO incident): the previous code stored
        # price strings here as placeholders, which made the stops-health
        # endpoint report 366 positions healthy while Binance had only 12
        # positions and 2 close orders. IDs are validated against the
        # Binance algo-order pattern via RiskOrderIds — non-matching values
        # are rejected at the boundary so the bug cannot recur.
        from tradeengine.position_health_guard import RiskOrderIds

        validated = RiskOrderIds(
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

        record = self.strategy_positions.get(strategy_position_id)
        if record is None:
            logger.warning(
                "set_strategy_position_orders: %s not in memory — skipping in-memory update",
                strategy_position_id,
            )
        else:
            if validated.sl_order_id is not None:
                record["sl_order_id"] = validated.sl_order_id
            if validated.tp_order_id is not None:
                record["tp_order_id"] = validated.tp_order_id

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
            position["exit_time"] = datetime.now(UTC)
            position["exit_order_id"] = exit_order_id
            position["close_reason"] = close_reason
            position["realized_pnl"] = pnl
            position["realized_pnl_pct"] = pnl_pct

            # Persist to Data Manager
            await self._update_strategy_position_closure(strategy_position_id, position)

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

    async def get_open_strategy_positions_by_exchange_key(
        self, exchange_position_key: str
    ) -> list[dict[str, Any]]:
        """Get all open strategy positions for a given exchange position

        Args:
            exchange_position_key: Exchange position key (e.g., "BTCUSDT_LONG")

        Returns:
            List of open strategy position dicts
        """
        try:
            open_positions = []

            # First, check in-memory strategy positions
            for strategy_position_id, position in self.strategy_positions.items():
                if (
                    position.get("exchange_position_key") == exchange_position_key
                    and position.get("status") == "open"
                ):
                    open_positions.append(position)

            # Note: Data Manager fallback query removed - positions are managed in memory

            logger.info(
                f"Found {len(open_positions)} open strategy positions for {exchange_position_key}"
            )
            return open_positions

        except Exception as e:
            logger.error(
                f"Error getting open strategy positions for {exchange_position_key}: {e}"
            )
            return []

    async def _persist_strategy_position(self, position: dict[str, Any]) -> None:
        """Persist strategy position to Data Manager"""
        result = await position_client.create_position(position)
        if result.ok:
            logger.debug(
                "Persisted strategy position %s to Data Manager",
                position.get("strategy_position_id"),
            )
        else:
            logger.error(
                "Failed to persist strategy position %s: %s",
                position.get("strategy_position_id"),
                result.error,
            )
            _on_persist_failure(result, position)

    async def _update_strategy_position_closure(
        self, strategy_position_id: str, position: dict[str, Any]
    ) -> None:
        """Update strategy position closure details in Data Manager"""
        result = await position_client.update_position(strategy_position_id, position)
        if result.ok:
            logger.debug(
                "Updated strategy position closure for %s via Data Manager",
                strategy_position_id,
            )
        else:
            logger.error(
                "Failed to update strategy position closure %s: %s",
                strategy_position_id,
                result.error,
            )
            _on_persist_failure(result, position)

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
                    "first_entry_time": datetime.now(UTC),
                    "last_update_time": datetime.now(UTC),
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
                position["last_update_time"] = datetime.now(UTC)
                position["total_contributions"] += 1

                if strategy_id not in position["contributing_strategies"]:
                    position["contributing_strategies"].append(strategy_id)

            # Persist to Data Manager
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
            position["last_update_time"] = datetime.now(UTC)

            if position["current_quantity"] <= 0:
                position["status"] = "closed"
                logger.info(f"Exchange position {exchange_position_key} fully closed")

            # Persist to Data Manager
            await self._persist_exchange_position(exchange_position_key)

        except Exception as e:
            logger.error(f"Error reducing exchange position: {e}")

    async def _persist_exchange_position(self, exchange_position_key: str) -> None:
        """Persist exchange position to Data Manager"""
        position = self.exchange_positions[exchange_position_key]
        result = await position_client.create_position(position)
        if result.failed:
            logger.error(
                "Failed to persist exchange position %s: %s",
                exchange_position_key,
                result.error,
            )
            _on_persist_failure(result, position)

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
                "contribution_time": datetime.now(UTC),
                "position_sequence": sequence,
                "exchange_quantity_before": qty_before,
                "exchange_quantity_after": qty_after,
                "status": "active",
            }

            # Store in memory
            if exchange_position_key not in self.contributions:
                self.contributions[exchange_position_key] = []
            self.contributions[exchange_position_key].append(contribution)

            # Persist to Data Manager
            contribution_data = {
                "contribution_id": contribution_id,
                "strategy_position_id": strategy_position_id,
                "exchange_position_key": exchange_position_key,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "position_side": position_side,
                "contribution_quantity": quantity,
                "contribution_entry_price": price,
                "contribution_time": contribution["contribution_time"],
                "position_sequence": sequence,
                "exchange_quantity_before": qty_before,
                "exchange_quantity_after": qty_after,
                "status": "active",
            }
            result = await position_client.create_position(contribution_data)
            if result.failed:
                logger.error(
                    "Failed to persist contribution %s for %s: %s",
                    contribution_id,
                    symbol,
                    result.error,
                )
                _on_persist_failure(result, contribution_data)

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
        update_data = {
            "status": "closed",
            "exit_time": datetime.now(UTC),
            "exit_price": exit_price,
            "contribution_pnl": pnl,
            "contribution_pnl_pct": pnl_pct,
            "close_reason": close_reason,
        }
        result = await position_client.update_position(
            strategy_position_id, update_data
        )
        if result.failed:
            logger.error(
                "Failed to close contribution %s: %s",
                strategy_position_id,
                result.error,
            )
            _on_persist_failure(result, update_data)

    def get_all_open_strategy_positions(self) -> list[dict[str, Any]]:
        """Return a shallow copy of all in-memory positions with status == 'open'."""
        return [
            dict(pos)
            for pos in self.strategy_positions.values()
            if pos.get("status") == "open"
        ]

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
