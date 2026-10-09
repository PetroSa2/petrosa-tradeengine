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

import inspect
import logging
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from petrosa_contracts import Signal

from contracts.order import TradeOrder
from shared.constants import TE_EXCHANGE_TRUTH_STORE_ENABLED, UTC

# Import Data Manager position client
from shared.mysql_client import position_client
from shared.retry import PersistResult
from tradeengine.exchange_truth_store import ExchangeTruthStore
from tradeengine.metrics import (
    otel_position_persist_failed,
    position_persist_failed_total,
    strategy_attribution_persist_failures_total,
)
from tradeengine.services.alert_publisher import alert_publisher
from tradeengine.services.execution_event_publisher import execution_event_publisher
from tradeengine.services.persist_retry_queue import PendingWrite, persist_retry_queue

logger = logging.getLogger(__name__)

_EIGHT_PLACES = Decimal("0.00000001")


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _money(value: Any) -> Decimal:
    return _decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _on_persist_failure(result: PersistResult, position_data: dict[str, Any]) -> None:
    """Emit metric + alert + enqueue retry on a failed persist. Never raises."""
    logger.error(
        "Position persistence failed operation=%s symbol=%s position_id=%s reason=%s error=%s",
        result.operation,
        result.symbol or position_data.get("symbol", "unknown"),
        result.position_id,
        result.reason or "unknown",
        result.error,
    )
    try:
        sym = result.symbol or str(position_data.get("symbol", "unknown"))
        path = (
            "strategy_positions"
            if "strategy_position" in result.operation
            or result.operation == "update_position"
            else "position_contributions"
            if "contribution" in result.operation
            else "exchange_positions"
        )
        strategy_attribution_persist_failures_total.labels(
            path=path,
            operation=result.operation,
            reason=result.reason or "unknown",
        ).inc()
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
        logger.error("Metric emission failed for persist_failed: %s", exc)

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
        logger.error("Alert publish failed for persist_failed: %s", exc)

    try:
        pw_data = dict(position_data)
        # create_position takes the payload dict as-is; update_position and
        # update_position_risk_orders need a position_id that is not itself
        # a field of the update payload — stash it so the registered retry
        # handler (persist_retry_queue.register_default_handlers) can pop it
        # back out before calling through.
        if result.operation != "create_position" and result.position_id:
            pw_data["_retry_position_id"] = result.position_id
        pw = PendingWrite(
            operation=result.operation,
            data=pw_data,
            symbol=result.symbol or str(position_data.get("symbol", "")),
            position_id=result.position_id,
            last_error=result.error,
            order_id=str(position_data.get("entry_order_id", "")),
            trade_id=str(position_data.get("trade_id", "")),
            idempotency_key=str(position_data.get("idempotency_key", "")),
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
        self._processed_close_keys: set[tuple[str, str, str]] = set()
        self._processed_fill_keys: set[tuple[str, str]] = set()
        self.unattributed: dict[str, Decimal] = {}
        # AC4 (#459 — 446-C): injected by Dispatcher after UserDataStreamConsumer starts.
        self.exchange_truth_store: ExchangeTruthStore | None = None

    @staticmethod
    async def _persist_call(
        method_name: str, legacy_name: str, *args: Any
    ) -> PersistResult:
        method = getattr(position_client, method_name)
        if not inspect.iscoroutinefunction(method):
            method = getattr(position_client, legacy_name)
        return await method(*args)

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

            # Extract execution details.
            # #557: same None-vs-missing gap as #548 — an unfilled MARKET
            # order returns fill_price explicitly None (key present), so
            # dict.get()'s default never applies and float(None) raises.
            fill_price = execution_result.get("fill_price")
            if fill_price is None:
                fill_price = signal.current_price
            entry_price = float(fill_price)
            entry_quantity = float(execution_result.get("amount", signal.quantity))
            entry_order_id = execution_result.get("order_id")
            entry_fee = _money(
                execution_result.get("entry_fee", execution_result.get("commission"))
            )
            entry_trade_id = str(execution_result.get("trade_id", "")) or None

            # #505: SHORT positions were stored with an unsigned (positive)
            # ``entry_quantity`` while ``side`` was tracked separately, so a
            # SHORT could be recorded as ``side="SHORT", entry_quantity=+9470.2``.
            # Consumers that infer direction from the *sign* of the quantity
            # (one-way / BOTH-mode reconcilers) then mis-classified live shorts.
            #
            # ``entry_quantity`` stays UNSIGNED on purpose — it is passed as the
            # Binance order ``amount=`` in the close/reduce paths
            # (position_health_guard, _update_exchange_position, _create_contribution)
            # which require a positive magnitude. Instead we store an explicit,
            # documented ``signed_quantity`` alongside it so any sign-based
            # inference has a single, consistent source of truth:
            #   LONG  -> +magnitude
            #   SHORT -> -magnitude
            _magnitude = abs(float(entry_quantity))
            signed_quantity = -_magnitude if position_side == "SHORT" else _magnitude

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
                # #531: persist decision_id at open so the OCO/close path can
                # publish a `filled` execution event the data-manager consumer
                # will accept (events with an empty decision_id are dropped at
                # data_manager/models/execution_event.py:88).
                "decision_id": signal.decision_id,
                "symbol": signal.symbol,
                "side": position_side,
                "entry_quantity": entry_quantity,
                # #505: signed convention (LONG>0, SHORT<0) for safe sign-based
                # inference; ``entry_quantity`` remains unsigned for order amounts.
                "signed_quantity": signed_quantity,
                "entry_price": entry_price,
                "entry_time": datetime.now(UTC),
                "entry_order_id": entry_order_id,
                "entry_fee": entry_fee,
                "trade_id": entry_trade_id,
                "position_id": order.position_id,
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
                # petrosa_k8s#1130: echo of the CIO-assigned position_id
                # (set as Signal.client_order_id by the translator, #1127).
                # Persisted here so a later close can round-trip it back to
                # CIO on the execution.events.<strategy_id> payload, letting
                # PortfolioTracker.record_exit / PositionReviewLoop.remove_position
                # key off the same identifier CIO used at admission time.
                "client_order_id": getattr(signal, "client_order_id", None),
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
                entry_fee=entry_fee,
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
        exit_price: float | None,
        exit_quantity: float | None = None,
        close_reason: str = "manual",
        exit_order_id: str | None = None,
        pnl_unknown: bool = False,
        exit_fee: Any = None,
        trade_id: str | None = None,
        exit_time: datetime | None = None,
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

            close_key = (
                strategy_position_id,
                str(exit_order_id or ""),
                str(trade_id or ""),
            )
            if exit_order_id and trade_id and close_key in self._processed_close_keys:
                return {
                    "strategy_position_id": strategy_position_id,
                    "idempotent": True,
                }
            if position.get("status") == "closed":
                return {
                    "strategy_position_id": strategy_position_id,
                    "idempotent": True,
                    "position_status": "closed",
                }

            # Calculate exit quantity
            if exit_quantity is None:
                exit_quantity = position["entry_quantity"] - position.get(
                    "exit_quantity", 0
                )
            exit_quantity = min(
                _decimal(exit_quantity),
                _decimal(position["entry_quantity"])
                - _decimal(position.get("exit_quantity", 0)),
            )

            # Calculate PnL
            entry_price = _decimal(position["entry_price"])
            entry_quantity = _decimal(position["entry_quantity"])
            exit_price_decimal = (
                _decimal(exit_price) if exit_price is not None else None
            )

            if pnl_unknown or exit_price_decimal is None:
                pnl = None
            elif position["side"] == "LONG":
                pnl = (exit_price_decimal - entry_price) * exit_quantity
            else:  # SHORT
                pnl = (entry_price - exit_price_decimal) * exit_quantity

            gross_pnl = None if pnl is None else _money(pnl)
            entry_fee_total = _money(position.get("entry_fee"))
            exit_fee_value = _money(exit_fee)
            fee_for_close = (
                (entry_fee_total * exit_quantity / entry_quantity) + exit_fee_value
                if entry_quantity and not pnl_unknown
                else None
            )
            net_pnl = None if gross_pnl is None else _money(gross_pnl + fee_for_close)
            closed_quantity = _decimal(position.get("exit_quantity", 0)) + exit_quantity

            pnl_pct = (
                ((pnl / (entry_price * exit_quantity)) * 100 if entry_price > 0 else 0)
                if pnl is not None
                else None
            )

            # Update position
            position["status"] = (
                "closed" if closed_quantity >= entry_quantity else "partially_closed"
            )
            position["exit_quantity"] = float(closed_quantity)
            position["exit_price"] = (
                float(exit_price_decimal) if exit_price_decimal is not None else None
            )
            position["exit_time"] = exit_time or datetime.now(UTC)
            position["exit_order_id"] = exit_order_id
            position["trade_id"] = trade_id
            position["close_reason"] = close_reason
            position["gross_realized_pnl"] = gross_pnl
            position["realized_pnl"] = (
                _money(_decimal(position.get("realized_pnl")) + net_pnl)
                if net_pnl is not None
                else None
            )
            prior_commission = position.get("commission_total")
            position["commission_total"] = (
                _money(
                    entry_fee_total if prior_commission is None else prior_commission
                )
                + exit_fee_value
            )
            position["realized_pnl_pct"] = (
                _money(net_pnl / (entry_price * exit_quantity) * 100)
                if net_pnl is not None and entry_price and exit_quantity
                else None
            )

            # Persist to Data Manager
            await self._update_strategy_position_closure(strategy_position_id, position)

            # Update contribution
            if not pnl_unknown:
                await self._close_contribution(
                    strategy_position_id,
                    exit_price_decimal,
                    net_pnl,
                    position["realized_pnl_pct"],
                    close_reason,
                    exit_quantity=exit_quantity,
                    trade_id=trade_id,
                    exit_fee=exit_fee_value,
                    exit_order_id=exit_order_id,
                )

            # Update exchange position
            await self._reduce_exchange_position(
                position["exchange_position_key"],
                exit_quantity,
                float(
                    exit_price_decimal
                    if exit_price_decimal is not None
                    else entry_price
                ),
            )

            if exit_order_id and trade_id:
                self._processed_close_keys.add(close_key)

            pnl_text = "unknown" if pnl is None else f"${pnl:.2f}"
            pct_text = "unknown" if pnl_pct is None else f"{pnl_pct:.2f}%"
            logger.info(
                f"Closed strategy position {strategy_position_id}: "
                f"{close_reason} at {exit_price}, PnL: {pnl_text} ({pct_text})"
            )

            client_order_id = position.get("client_order_id") or position.get(
                "position_id"
            )
            await execution_event_publisher.publish(
                event_type="position_closed",
                strategy_id=str(position.get("strategy_id") or "unknown"),
                order_id=str(exit_order_id or ""),
                reason=close_reason,
                decision_id=position.get("decision_id"),
                timestamp=position["exit_time"],
                client_order_id=client_order_id,
                idempotency_key=(
                    f"position_closed:{exit_order_id}:{client_order_id}"
                    if exit_order_id and client_order_id
                    else None
                ),
                extra={
                    "position_id": position.get("position_id"),
                    "strategy_position_id": strategy_position_id,
                    "entry_order_id": position.get("entry_order_id"),
                    "closed_quantity": exit_quantity,
                    "remaining_quantity": max(
                        _decimal(position["entry_quantity"])
                        - _decimal(position.get("exit_quantity", 0)),
                        Decimal("0"),
                    ),
                    "exit_price": exit_price,
                    "exit_time": position["exit_time"],
                    "reason": close_reason,
                    "exit_order_id": exit_order_id,
                    "pnl_basis": "unknown" if pnl_unknown else "fifo_attributed",
                    "pnl": net_pnl,
                    "fee": exit_fee_value,
                },
            )

            return {
                "strategy_position_id": strategy_position_id,
                "strategy_id": position["strategy_id"],
                # #531: surface decision_id so the OCO close path can publish a
                # `filled` execution event the data-manager consumer accepts.
                "decision_id": position.get("decision_id"),
                "symbol": position["symbol"],
                "side": position["side"],
                "close_reason": close_reason,
                "entry_price": entry_price,
                "entry_order_id": position.get("entry_order_id"),
                "position_id": position.get("position_id"),
                "exit_price": exit_price,
                "quantity": exit_quantity,
                "closed_quantity": exit_quantity,
                "remaining_quantity": max(
                    _decimal(position["entry_quantity"])
                    - _decimal(position.get("exit_quantity", 0)),
                    Decimal("0"),
                ),
                "exit_time": position["exit_time"],
                "realized_pnl": position["realized_pnl"],
                "closed_pnl": net_pnl,
                "gross_realized_pnl": gross_pnl,
                "realized_pnl_pct": position["realized_pnl_pct"],
                "commission_total": position["commission_total"],
                # petrosa_k8s#1130: round-trip the CIO position_id + whether
                # this closure emptied the position (vs a partial scale-out)
                # so callers can decide whether to tell CIO the position is
                # fully gone (portfolio_tracker.record_exit / position_review_loop
                # .remove_position) or still open at a reduced size.
                "client_order_id": position.get("client_order_id"),
                "position_status": position["status"],
                "pnl_unknown": pnl_unknown,
                "pnl_basis": "unknown" if pnl_unknown else "fifo_attributed",
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
                if position.get(
                    "exchange_position_key"
                ) == exchange_position_key and position.get("status") in {
                    "open",
                    "partially_closed",
                }:
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
        result = await self._persist_call(
            "create_strategy_position", "create_position", position
        )
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
        result = await self._persist_call(
            "update_strategy_position",
            "update_position",
            strategy_position_id,
            position,
        )
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
            position["current_quantity"] -= float(quantity)
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
        result = await self._persist_call(
            "create_exchange_position", "create_position", position
        )
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
        entry_fee: Decimal = Decimal("0"),
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
                "entry_fee": entry_fee,
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
                "entry_fee": entry_fee,
                "contribution_time": contribution["contribution_time"],
                "position_sequence": sequence,
                "exchange_quantity_before": qty_before,
                "exchange_quantity_after": qty_after,
                "status": "active",
            }
            result = await self._persist_call(
                "create_position_contribution", "create_position", contribution_data
            )
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
        *,
        exit_quantity: Decimal | None = None,
        trade_id: str | None = None,
        exit_fee: Decimal = Decimal("0"),
        exit_order_id: str | None = None,
    ) -> None:
        """Close contribution record when strategy position closes"""
        update_data = {
            "status": "closed",
            "exit_time": datetime.now(UTC),
            "exit_price": exit_price,
            "contribution_pnl": pnl,
            "contribution_pnl_pct": pnl_pct,
            "trade_id": trade_id,
            "exit_order_id": exit_order_id,
            "exit_fee": exit_fee,
            "close_reason": close_reason,
        }
        contribution_id = next(
            (
                item.get("contribution_id")
                for item in self.contributions.get(
                    self.strategy_positions.get(strategy_position_id, {}).get(
                        "exchange_position_key", ""
                    ),
                    [],
                )
                if item.get("strategy_position_id") == strategy_position_id
            ),
            strategy_position_id,
        )
        result = await self._persist_call(
            "update_position_contribution",
            "update_position",
            str(contribution_id),
            update_data,
        )
        if result.failed:
            logger.error(
                "Failed to close contribution %s: %s",
                strategy_position_id,
                result.error,
            )
            _on_persist_failure(result, update_data)

        for item in self.contributions.get(
            self.strategy_positions.get(strategy_position_id, {}).get(
                "exchange_position_key", ""
            ),
            [],
        ):
            if item.get("strategy_position_id") == strategy_position_id:
                item.update(update_data)

    async def close_exchange_fill(
        self,
        exchange_position_key: str,
        exit_price: Any,
        exit_quantity: Any,
        *,
        exit_order_id: str | None = None,
        trade_id: str | None = None,
        close_reason: str = "manual",
        exit_fee: Any = None,
    ) -> dict[str, Any]:
        """Allocate one exchange fill across open strategy positions FIFO."""
        fill_key = (str(exit_order_id or ""), str(trade_id or ""))
        if exit_order_id and trade_id and fill_key in self._processed_fill_keys:
            return {
                "allocated_quantity": _money(0),
                "unattributed": _money(0),
                "idempotent": True,
            }

        remaining = _decimal(exit_quantity)
        fee_remaining = _money(exit_fee)
        positions = sorted(
            await self.get_open_strategy_positions_by_exchange_key(
                exchange_position_key
            ),
            key=lambda item: next(
                (
                    contribution.get("position_sequence", 0)
                    for contribution in self.contributions.get(
                        exchange_position_key, []
                    )
                    if contribution.get("strategy_position_id")
                    == item.get("strategy_position_id")
                ),
                0,
            ),
        )
        allocations: list[dict[str, Any]] = []
        planned: list[tuple[dict[str, Any], Decimal]] = []
        for position in positions:
            if remaining <= 0:
                break
            available = _decimal(position.get("entry_quantity")) - _decimal(
                position.get("exit_quantity", 0)
            )
            quantity = min(available, remaining)
            planned.append((position, quantity))
            remaining -= quantity

        remaining = _decimal(exit_quantity)
        for index, (position, quantity) in enumerate(planned):
            if remaining <= 0:
                break
            fee = (
                fee_remaining
                if index == len(planned) - 1
                else _money(_decimal(exit_fee) * quantity / _decimal(exit_quantity))
            )
            fee_remaining -= fee
            allocation = await self.close_strategy_position(
                    strategy_position_id=position["strategy_position_id"],
                    exit_price=exit_price,
                    exit_quantity=quantity,
                    close_reason=close_reason,
                    exit_order_id=exit_order_id,
                    trade_id=trade_id,
                    exit_fee=fee,
                )
            allocations.append(allocation)
            remaining -= quantity

        if exit_order_id and trade_id:
            self._processed_fill_keys.add(fill_key)
        if remaining:
            day = datetime.now(UTC).date().isoformat()
            self.unattributed[day] = _money(self.unattributed.get(day, 0) + remaining)
            logger.warning(
                "Exit fill %s exceeded open rows by %s on %s",
                exit_order_id,
                remaining,
                exchange_position_key,
            )
        return {
            "allocations": allocations,
            "allocated_quantity": _money(_decimal(exit_quantity) - remaining),
            "unattributed": _money(remaining),
        }

    def get_all_open_strategy_positions(self) -> list[dict[str, Any]]:
        """Return a shallow copy of all in-memory positions with status == 'open'."""
        return [
            dict(pos)
            for pos in self.strategy_positions.values()
            if pos.get("status") in {"open", "partially_closed"}
        ]

    async def evict_ghost_position(
        self, strategy_position_id: str, reason: str = "no_exchange_position"
    ) -> bool:
        """Evict a strategy position that has no matching exchange position (#480).

        Unlike ``close_strategy_position``, this does NOT touch the aggregated
        exchange_position record because the underlying exchange position
        never existed (or was already closed externally). The in-memory row
        is removed and Data Manager is updated with ``status="closed_externally"``
        so the audit journal still shows what happened.

        Returns True if the position was found and evicted, False otherwise.
        Never raises — eviction errors are logged but must not break the
        reconcile loop.
        """
        position = self.strategy_positions.get(strategy_position_id)
        if position is None:
            return False

        position["status"] = "closed_externally"
        position["exit_time"] = datetime.now(UTC)
        position["close_reason"] = reason

        try:
            await self._update_strategy_position_closure(strategy_position_id, position)
        except Exception as exc:
            logger.error(
                "evict_ghost_position: Data Manager update failed for %s: %s",
                strategy_position_id,
                exc,
            )

        self.strategy_positions.pop(strategy_position_id, None)
        logger.info(
            "Evicted ghost strategy position %s (%s %s) — reason=%s",
            strategy_position_id,
            position.get("symbol"),
            position.get("side"),
            reason,
        )
        return True

    def get_strategy_position(self, strategy_position_id: str) -> dict[str, Any] | None:
        """Get strategy position by ID"""
        return self.strategy_positions.get(strategy_position_id)

    def get_strategy_position_by_entry_order_id(
        self, entry_order_id: str
    ) -> dict[str, Any] | None:
        """Find a strategy position by its entry order id (#531).

        Used by the user-data-stream fill path to recover strategy_id and
        decision_id for an entry fill, since the raw ORDER_TRADE_UPDATE event
        carries neither. Order ids are compared as strings because Binance
        returns them as ints on the stream but they are stored as strings.
        """
        if not entry_order_id:
            return None
        target = str(entry_order_id)
        for pos in self.strategy_positions.values():
            if str(pos.get("entry_order_id")) == target:
                return pos
        return None

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
