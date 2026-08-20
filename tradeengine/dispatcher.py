import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal

from opentelemetry import trace
from prometheus_client import Counter, Histogram

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import Signal, TimeInForce
from shared.audit import audit_logger
from shared.config import Settings, settings
from shared.constants import (
    OCO_CANCEL_RETRY_ATTEMPTS,
    OCO_CANCEL_RETRY_BACKOFF_MULTIPLIER,
    OCO_CANCEL_RETRY_BASE_DELAY,
    OCO_CANCEL_RETRY_MAX_DELAY,
    UTC,
)
from shared.distributed_lock import distributed_lock_manager
from shared.logger import get_logger
from tradeengine.exchange_truth_store import ExchangeTruthStore, UserDataStreamConsumer
from tradeengine.leverage_bound_guard import LeverageBoundGuard
from tradeengine.metrics import (
    atomic_rollback_failed_total,
    dispatcher_thrash_circuit_open_total,
    oco_cancel_retry_exhausted_total,
    oco_pair_age_seconds,
    order_execution_latency_seconds,
    order_failures_total,
    order_placement_skipped_total,
    orders_executed_by_type,
    otel_oco_pair_age_seconds,
    risk_checks_total,
    risk_rejections_total,
    strategy_close_blocked_no_exchange_position_total,
)
from tradeengine.order_manager import OrderManager
from tradeengine.position_manager import PositionManager
from tradeengine.services.alert_publisher import alert_publisher
from tradeengine.services.execution_event_publisher import (
    EventType as ExecutionEventType,
    execution_event_publisher,
)
from tradeengine.services.halt_suspected_detector import halt_suspected_detector
from tradeengine.services.heartbeat_monitor import HeartbeatMonitor
from tradeengine.signal_aggregator import SignalAggregator
from tradeengine.strategy_position_manager import strategy_position_manager
from tradeengine.strategy_position_reconciler import (
    StrategyPositionReconciler,
    _has_matching_exchange_position,
)
from tradeengine.thrash_guard import ThrashCircuitBreaker

# Prometheus metrics for signal flow tracking
signals_received = Counter(
    "tradeengine_signals_received_total",
    "Total signals received by the dispatcher",
    ["strategy", "symbol", "action"],
)

signals_processed = Counter(
    "tradeengine_signals_processed_total",
    "Total signals processed by the dispatcher",
    ["status", "action"],
)

orders_executed = Counter(
    "tradeengine_orders_executed_total",
    "Total orders executed",
    ["symbol", "side", "status"],
)

order_execution_time = Histogram(
    "tradeengine_order_execution_seconds",
    "Time taken to execute orders",
    ["symbol", "side"],
)

binance_api_calls = Counter(
    "tradeengine_binance_api_calls_total",
    "Total Binance API calls",
    ["operation", "status"],
)

signals_duplicate = Counter(
    "tradeengine_signals_duplicate_total",
    "Total duplicate signals detected and rejected",
    ["strategy", "symbol", "action"],
)

# OpenTelemetry tracer for business context spans
tracer = trace.get_tracer(__name__)


class OCOManager:
    """Manages OCO (One-Cancels-the-Other) logic for SL/TP orders"""

    def __init__(self, exchange: Any, logger: logging.Logger, dispatcher: Any = None):
        self.exchange = exchange
        self.logger = logger
        self.dispatcher = dispatcher  # Reference to dispatcher for position management
        # CHANGED: Now supports multiple OCO pairs per exchange position (list of dicts)
        self.active_oco_pairs: dict[
            str, list[dict[str, Any]]
        ] = {}  # exchange_position_key -> [oco_info, ...]
        # #371: CONDITIONAL entries whose OCO must wait for FILLED. Key = exchange entry order id.
        self.pending_entries: dict[str, dict[str, Any]] = {}
        self.monitoring_task: asyncio.Task[Any] | None = None
        self.monitoring_active = False
        # #534 (H6 of #977): WS-driven OCO completion nudge. Set by the
        # user-data-stream fill callback when a FILLED SL/TP leg belongs to a
        # tracked OCO pair, so _monitor_orders wakes early instead of waiting
        # the full 2s poll interval. The poll still performs all cancel/close
        # decisions — this event only shortens latency (poll is the backstop).
        self._oco_wake_event: asyncio.Event = asyncio.Event()

    def notify_oco_leg_fill(self, symbol: str, order_id: str) -> None:
        """#534 (H6 of #977): wake _monitor_orders when a FILLED SL/TP leg
        observed on the user-data stream belongs to a tracked OCO pair.

        Only sets the wake event when ``order_id`` matches an SL or TP order of
        an active pair for ``symbol`` — a fill for an unrelated order does not
        shorten the poll interval. Setting an already-set event is a no-op, and
        the poll remains the authoritative decision-maker, so this is safe to
        call for every leg fill (idempotent, never cancels/closes directly).
        """
        for oco_list in self.active_oco_pairs.values():
            for oco in oco_list:
                if oco.get("symbol") != symbol:
                    continue
                if order_id in (
                    str(oco.get("sl_order_id", "")),
                    str(oco.get("tp_order_id", "")),
                ):
                    self._oco_wake_event.set()
                    self.logger.info(
                        "#534: WS ORDER_TRADE_UPDATE fill on OCO leg %s (%s) "
                        "— nudging monitor to re-poll immediately",
                        order_id,
                        symbol,
                    )
                    return

    async def _exchange_has_protective_pair(
        self, symbol: str, position_side: str
    ) -> bool:
        """AC-2 (#550): return True iff Binance already holds a reduceOnly
        STOP + TAKE_PROFIT protective pair for ``(symbol, position_side)``.

        Queries ``openAlgoOrders`` (protective legs are ``closePosition=True``
        conditional/algo orders, NOT standard ``openOrders``) and checks that
        at least one ``STOP_MARKET`` and one ``TAKE_PROFIT_MARKET`` order exist
        for the given ``positionSide``. This is the exchange-truth counterpart
        to the process-local ``active_oco_pairs`` dedup guard: it prevents the
        orphan over-arm that occurs when local state is empty/stale after a
        restart but the exchange already covers the position.

        Best-effort: any lookup error propagates to the caller, which falls
        through to placement rather than dropping a real protective pair.
        """
        algo_orders = await self.exchange.get_open_algo_orders(symbol)
        if not algo_orders:
            return False

        has_stop = False
        has_take_profit = False
        for order in algo_orders:
            # positionSide gate: HEDGE mode yields LONG/SHORT rows; ONE-WAY
            # yields BOTH. Treat BOTH as matching any requested side so the
            # gate still dedups one-way accounts.
            order_side = str(order.get("positionSide", "")).upper()
            if order_side not in (position_side.upper(), "BOTH", ""):
                continue
            order_type = str(order.get("type") or order.get("orderType") or "").upper()
            if order_type in ("STOP_MARKET", "STOP"):
                has_stop = True
            elif order_type in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                has_take_profit = True
            if has_stop and has_take_profit:
                return True

        return has_stop and has_take_profit

    def _sync_oco_pairs_gauge(self) -> None:
        """AC (#550): set active_oco_pairs_per_position from the current
        active_oco_pairs map so the gauge reflects exchange truth (every
        covered position gets a row) rather than only positions armed by a
        live placement in this process.

        Emits one gauge sample per (symbol, position_side) that has at least
        one ``active`` OCO entry, using the count of active entries as the
        value. Best-effort — a metrics failure must never break reconcile.
        """
        try:
            from tradeengine.metrics import active_oco_pairs_per_position
        except Exception:
            return
        for key, oco_list in self.active_oco_pairs.items():
            active_entries = [o for o in oco_list if o.get("status") == "active"]
            if not active_entries:
                continue
            symbol = active_entries[0].get("symbol")
            position_side = active_entries[0].get("position_side")
            if not symbol or not position_side:
                # Fall back to parsing the "SYMBOL_SIDE" key.
                parsed = key.rsplit("_", 1)
                if len(parsed) == 2:
                    symbol, position_side = parsed
            if not symbol or not position_side:
                continue
            try:
                active_oco_pairs_per_position.labels(
                    symbol=symbol,
                    position_side=position_side,
                    exchange="binance",
                ).set(len(active_entries))
            except Exception:
                self.logger.debug(
                    "Failed to emit active_oco_pairs_per_position for %s",
                    key,
                    exc_info=True,
                )

    async def place_oco_orders(
        self,
        position_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_loss_price: float,
        take_profit_price: float,
        strategy_position_id: str | None = None,  # NEW: Link to strategy position
        entry_price: float | None = None,  # NEW: Strategy's entry price for P&L calc
    ) -> dict[str, Any]:
        """
        Place SL/TP orders that will cancel each other (OCO behavior)

        Args:
            position_id: Unique identifier for the position
            symbol: Trading symbol (e.g., BTCUSDT)
            position_side: LONG or SHORT
            quantity: Position size
            stop_loss_price: Stop loss trigger price
            take_profit_price: Take profit target price
            strategy_position_id: Strategy position UUID (for tracking)
            entry_price: Strategy's actual entry price (for P&L calculation)

        Returns:
            Dict with sl_order_id and tp_order_id
        """

        exchange_position_key = f"{symbol}_{position_side}"

        # AC3 of #445: gate placement on exchange-confirmed position state.
        # Without this, a restart after a Mongo blip or stale local state can
        # fire reduceOnly/closePosition orders against positions Binance no
        # longer holds → APIError(-4509) "TIF GTE can only be used with open
        # positions" loop. Skipping is safer than churning.
        #
        # Ships off by default — same pattern as the rest of #445 — because
        # the freshly-filled-entry race (entry order fills → OCO placement
        # races positionRisk propagation) can produce a transient "no
        # position" reading on Binance that would otherwise cause the gate
        # to skip arming a real position. Operator flips
        # TE_OCO_AC3_GATE_ENABLED=1 after canary verification.
        _ac3_gate_enabled = os.getenv("TE_OCO_AC3_GATE_ENABLED", "0") == "1"
        if (
            _ac3_gate_enabled
            and self.dispatcher is not None
            and hasattr(self.dispatcher, "_fetch_binance_position_qty")
        ):
            try:
                live_qty = await self.dispatcher._fetch_binance_position_qty(
                    symbol, position_side
                )
            except Exception:
                # Lookup failed — fall through to placement attempt rather
                # than silently dropping the OCO. _fetch_binance_position_qty
                # is already best-effort and returns 0.0 on failure, but we
                # defend against future refactors that could raise here.
                live_qty = -1.0
            if 0.0 <= live_qty < 1e-9:
                self.logger.warning(
                    "OCO placement skipped: Binance reports no open position "
                    "for %s %s (AC3 of #445 — prevents -4509 GTE loop)",
                    symbol,
                    position_side,
                )
                return {
                    "sl_order_id": None,
                    "tp_order_id": None,
                    "status": "skipped_no_position_on_exchange",
                    "symbol": symbol,
                    "position_side": position_side,
                }

        # AC-2 (#352): one OCO pair per exchange position. Multiple strategies stacking
        # independent SL/TP pairs on the same position hits the Binance 10-order limit.
        existing_pairs = self.active_oco_pairs.get(exchange_position_key, [])
        active_pairs = [p for p in existing_pairs if p.get("status") == "active"]
        if active_pairs:
            self.logger.warning(
                f"⛔ OCO dedup: {exchange_position_key} already has {len(active_pairs)} "
                f"active OCO pair(s). Skipping new placement for strategy {strategy_position_id}."
            )
            return {
                "status": "rejected",
                "error": "duplicate_oco",
                "exchange_position_key": exchange_position_key,
                "active_pairs": len(active_pairs),
            }

        # AC-2 (#550): consult Binance truth before placing. The local
        # active_oco_pairs dedup above trusts process-local state only, which
        # resets on restart and is fed solely by placement events. After a
        # restart (or any local/exchange divergence) the local map can be empty
        # for a position that Binance already covers → the engine re-arms and
        # immediately cancels the extra legs as orphans (oco_orphan_leg_total).
        # Query openAlgoOrders for (symbol, positionSide); if a reduceOnly
        # STOP + TP pair already exists on the exchange, skip placement.
        #
        # On by default (unlike the AC3 positionRisk gate) because it only
        # skips when the exchange *already* holds both protective legs — there
        # is no freshly-filled-entry race that could make it skip a real
        # unprotected position. Operator can disable with
        # TE_OCO_EXCHANGE_TRUTH_DEDUP=0 for rollback.
        _exchange_truth_dedup = os.getenv("TE_OCO_EXCHANGE_TRUTH_DEDUP", "1") == "1"
        if _exchange_truth_dedup and hasattr(self.exchange, "get_open_algo_orders"):
            try:
                if await self._exchange_has_protective_pair(symbol, position_side):
                    self.logger.warning(
                        "⛔ OCO dedup (exchange truth #550): %s already has a "
                        "reduceOnly STOP+TP pair on Binance (openAlgoOrders). "
                        "Skipping placement for strategy %s to avoid orphan "
                        "over-arm.",
                        exchange_position_key,
                        strategy_position_id,
                    )
                    return {
                        "status": "skipped_exchange_pair_exists",
                        "exchange_position_key": exchange_position_key,
                        "sl_order_id": None,
                        "tp_order_id": None,
                        "symbol": symbol,
                        "position_side": position_side,
                    }
            except Exception:
                # Best-effort: never let an exchange-truth lookup failure block
                # placement of a real protective pair. Fall through to place.
                self.logger.debug(
                    "OCO exchange-truth dedup lookup failed for %s — "
                    "falling through to placement",
                    exchange_position_key,
                    exc_info=True,
                )

        self.logger.info(
            f"Placing OCO orders: symbol={symbol}, position_side={position_side}, "
            f"position_id={position_id}, strategy_position_id={strategy_position_id}, "
            f"quantity={quantity}, stop_loss_price={stop_loss_price}, "
            f"take_profit_price={take_profit_price}, entry_price={entry_price}"
        )

        # Defense-in-depth (#479): when the caller has provided an explicit
        # entry_price, refuse to ship a known-wrong-side leg. The dispatcher
        # direction validator at _place_risk_management_orders should already
        # have corrected wrong-side prices before reaching here — this is the
        # belt to its braces. We intentionally do NOT fall back to fetching
        # current market price here because doing so re-triggers on test
        # mocks where _get_current_price returns a MagicMock float-coerced
        # to 1.0; the dispatcher already does the market fallback upstream.
        ref_for_check = entry_price if (entry_price and entry_price > 0) else None
        if ref_for_check and ref_for_check > 0 and position_side in ("LONG", "SHORT"):
            sl_wrong = (
                position_side == "LONG" and stop_loss_price >= ref_for_check
            ) or (position_side == "SHORT" and stop_loss_price <= ref_for_check)
            tp_wrong = (
                position_side == "LONG" and take_profit_price <= ref_for_check
            ) or (position_side == "SHORT" and take_profit_price >= ref_for_check)
            if sl_wrong or tp_wrong:
                self.logger.error(
                    "OCO REFUSED: %s %s direction inversion detected "
                    "(ref=%s, SL=%s wrong=%s, TP=%s wrong=%s). Refusing to "
                    "ship — upstream caller must correct before resubmitting.",
                    symbol,
                    position_side,
                    ref_for_check,
                    stop_loss_price,
                    sl_wrong,
                    take_profit_price,
                    tp_wrong,
                )
                return {
                    "status": "rejected",
                    "error": "direction_inversion",
                    "symbol": symbol,
                    "position_side": position_side,
                    "reference_price": ref_for_check,
                    "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price,
                    "sl_wrong_side": sl_wrong,
                    "tp_wrong_side": tp_wrong,
                }

        # Determine order sides based on position side
        if position_side == "LONG":
            sl_side = OrderSide.SELL
            tp_side = OrderSide.SELL
        else:  # SHORT
            sl_side = OrderSide.BUY
            tp_side = OrderSide.BUY

        # Place Stop Loss order
        sl_order = TradeOrder(
            symbol=symbol,
            side=sl_side,
            type=OrderType.STOP,
            amount=quantity,
            target_price=stop_loss_price,
            stop_loss=stop_loss_price,
            take_profit=None,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=f"oco_sl_{position_id}_{int(time.time())}",
            status=OrderStatus.PENDING,
            filled_amount=0.0,
            average_price=None,
            position_id=None,
            position_side=position_side,
            exchange="binance",
            reduce_only=True,
            time_in_force=TimeInForce.GTC,
            position_size_pct=None,
            simulate=False,
            updated_at=None,
        )

        # Place Take Profit order
        tp_order = TradeOrder(
            symbol=symbol,
            side=tp_side,
            type=OrderType.TAKE_PROFIT,
            amount=quantity,
            target_price=take_profit_price,
            stop_loss=None,
            take_profit=take_profit_price,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=f"oco_tp_{position_id}_{int(time.time())}",
            status=OrderStatus.PENDING,
            filled_amount=0.0,
            average_price=None,
            position_id=None,
            position_side=position_side,
            exchange="binance",
            reduce_only=True,
            time_in_force=TimeInForce.GTC,
            position_size_pct=None,
            simulate=False,
            updated_at=None,
        )

        # Execute both orders
        try:
            sl_result = await self.exchange.execute(sl_order)
            tp_result = await self.exchange.execute(tp_order)

            sl_order_id = sl_result.get("order_id")
            tp_order_id = tp_result.get("order_id")

            if sl_order_id and tp_order_id:
                # Store the OCO pair for monitoring
                if exchange_position_key not in self.active_oco_pairs:
                    self.active_oco_pairs[exchange_position_key] = []

                oco_info = {
                    "position_id": position_id,  # Store position_id for backward compatibility
                    "strategy_position_id": strategy_position_id,
                    "entry_price": (
                        float(entry_price) if entry_price is not None else 0.0
                    ),
                    "quantity": float(quantity) if quantity is not None else 0.0,
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "symbol": symbol,
                    "position_side": position_side,
                    "status": "active",
                    "created_at": time.time(),
                }

                self.active_oco_pairs[exchange_position_key].append(oco_info)

                self.logger.info("✅ OCO ORDERS PLACED SUCCESSFULLY")
                self.logger.info(
                    f"OCO pair added for strategy {strategy_position_id}: "
                    f"Total OCO pairs for {exchange_position_key}: "
                    f"{len(self.active_oco_pairs[exchange_position_key])}"
                )
                self.logger.info(
                    f"OCO orders placed: position_id={position_id}, "
                    f"strategy_position_id={strategy_position_id}, "
                    f"sl_order_id={sl_order_id}, tp_order_id={tp_order_id}"
                )

                # Export metrics
                from tradeengine.metrics import (
                    active_oco_pairs_per_position,
                    strategy_oco_placed_total,
                )
                from tradeengine.strategy_position_manager import (
                    strategy_position_manager,
                )

                # Get strategy_id from position manager
                if strategy_position_id and strategy_position_manager:
                    strategy_pos = strategy_position_manager.get_strategy_position(
                        strategy_position_id
                    )
                    if strategy_pos:
                        strat_id = strategy_pos.get("strategy_id", "unknown")
                        strategy_oco_placed_total.labels(
                            strategy_id=strat_id, symbol=symbol, exchange="binance"
                        ).inc()

                active_oco_pairs_per_position.labels(
                    symbol=symbol, position_side=position_side, exchange="binance"
                ).set(len(self.active_oco_pairs[exchange_position_key]))

                # Start monitoring if not already active
                if not self.monitoring_active:
                    await self.start_monitoring()

                return {
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "status": "success",
                }
            else:
                # #425 (RC#1 of #424): partial OCO failure — one leg posted, the other did not.
                # Cancel the surviving leg on Binance before returning to prevent an orphan
                # blocking the next placement attempt with -4130 ("open stop/TP already exists").
                surviving_leg: tuple[str, str] | None = None
                if sl_order_id and not tp_order_id:
                    surviving_leg = ("SL", sl_order_id)
                elif tp_order_id and not sl_order_id:
                    surviving_leg = ("TP", tp_order_id)

                if surviving_leg is not None:
                    leg_label, surviving_id = surviving_leg
                    self.logger.error(
                        f"⚠️ PARTIAL OCO FAILURE: {leg_label} leg posted "
                        f"(algoId={surviving_id}) but counterparty failed for "
                        f"{symbol} {position_side}. Cancelling surviving leg."
                    )
                    from tradeengine.metrics import (
                        oco_orphan_leg_total,
                        otel_oco_orphan_count,
                        otel_oco_orphan_leg,
                    )

                    try:
                        self.exchange.client._request_futures_api(
                            "delete",
                            "algoOrder",
                            signed=True,
                            force_params=True,
                            data={"symbol": symbol, "algoId": surviving_id},
                        )
                        self.logger.info(
                            f"✅ Cancelled orphan {leg_label} algoId={surviving_id} "
                            f"for {symbol} {position_side}"
                        )
                        # #482 AC2: every partial-OCO event ticks the counter so
                        # operators can see how often the path is non-atomic at
                        # the exchange, not just the unrecoverable subset.
                        oco_orphan_leg_total.labels(
                            symbol=symbol,
                            side=position_side,
                            leg=leg_label,
                            cancel_outcome="success",
                        ).inc()
                        # #497: OTel dual-export so Grafana Cloud alert fires
                        otel_oco_orphan_leg.add(
                            1,
                            {
                                "symbol": symbol,
                                "side": position_side,
                                "leg": leg_label,
                                "cancel_outcome": "success",
                            },
                        )
                    except Exception as cancel_err:
                        self.logger.error(
                            f"❌ FAILED to cancel orphan {leg_label} "
                            f"algoId={surviving_id} for {symbol} {position_side}: "
                            f"{cancel_err}"
                        )
                        # #482 AC2: cancel_outcome="failed" is the alert bucket
                        # — surviving leg still on Binance, position unhedged.
                        oco_orphan_leg_total.labels(
                            symbol=symbol,
                            side=position_side,
                            leg=leg_label,
                            cancel_outcome="failed",
                        ).inc()
                        # #497: OTel dual-export (failed bucket = page-worthy)
                        otel_oco_orphan_leg.add(
                            1,
                            {
                                "symbol": symbol,
                                "side": position_side,
                                "leg": leg_label,
                                "cancel_outcome": "failed",
                            },
                        )
                        # Track the unhedged orphan in the count gauge so the
                        # pre-existing `tradeengine-oco-pair-orphan` alert fires.
                        otel_oco_orphan_count.add(1)

                self.logger.error("❌ FAILED TO PLACE OCO ORDERS")
                self.logger.error(f"  SL Result: {sl_result}")
                self.logger.error(f"  TP Result: {tp_result}")

                # #504 (inverse of #482): a PARTIAL OCO failure means one leg
                # posted and was then atomically cancelled above to avoid the
                # -4509 orphan loop (#425/#482 — we deliberately keep that
                # cancel). The consequence is that the position is now FULLY
                # NAKED: entry filled, both protective legs gone. Previously
                # this returned an opaque {"status": "failed"} that the caller
                # (_place_risk_management_orders) treated identically to a
                # benign rejection, silently falling back to individual-order
                # placement with the SAME uncorrected prices — and when those
                # also failed (the 2026-07-16 -2021 storm) the position was
                # left naked with NO escalation. We now surface an explicit,
                # actionable naked/escalation signal so the caller hands off to
                # the exchange-authoritative naked-position remediator (re-arm
                # with corrected pricing, or flatten after the grace window)
                # instead of going silently unprotected.
                if surviving_leg is not None:
                    leg_label, surviving_id = surviving_leg
                    self.logger.error(
                        "🚨 POSITION NAKED after partial OCO failure for "
                        f"{symbol} {position_side}: {leg_label} leg was posted "
                        f"then cancelled (algoId={surviving_id}); no protection "
                        "remains. Flagging for remediation (re-arm/flatten)."
                    )
                    return {
                        "status": "failed",
                        "position_naked": True,
                        "requires_remediation": True,
                        "escalate": True,
                        "reason": "partial_oco_failure_surviving_leg_cancelled",
                        "symbol": symbol,
                        "position_side": position_side,
                        "cancelled_leg": leg_label,
                        "cancelled_algo_id": surviving_id,
                    }

                return {"status": "failed"}

        except Exception as e:
            self.logger.error(f"❌ ERROR PLACING OCO ORDERS: {e}")
            return {"status": "error", "error": str(e)}

    async def cancel_oco_pair(
        self, position_id: str, symbol: str = None, position_side: str = None
    ) -> bool:
        """
        Cancel ALL OCO pairs for a position (used for manual position closure)

        Args:
            position_id: Position identifier (legacy)
            symbol: Trading symbol
            position_side: Position side (LONG/SHORT)

        Returns:
            True if all OCO pairs were cancelled successfully
        """

        # Build exchange_position_key
        exchange_position_key = None
        if symbol and position_side:
            exchange_position_key = f"{symbol}_{position_side}"

        # Try to find OCO pairs
        oco_list = None
        if exchange_position_key and exchange_position_key in self.active_oco_pairs:
            oco_list = self.active_oco_pairs[exchange_position_key]
        elif position_id in self.active_oco_pairs:
            # Backward compatibility - old key structure
            oco_list = (
                [self.active_oco_pairs[position_id]]
                if isinstance(self.active_oco_pairs[position_id], dict)
                else self.active_oco_pairs[position_id]
            )

        if not oco_list:
            self.logger.warning(
                f"⚠️  No OCO pairs found for position {position_id} / {exchange_position_key}"
            )
            return False

        all_cancelled = True
        for oco_info in oco_list:
            if oco_info["status"] != "active":
                continue

            sl_order_id = oco_info["sl_order_id"]
            tp_order_id = oco_info["tp_order_id"]
            oco_symbol = oco_info["symbol"]

            self.logger.info(
                f"Cancelling OCO pair: symbol={oco_symbol}, "
                f"strategy_position_id={oco_info.get('strategy_position_id')}, "
                f"sl_order_id={sl_order_id}, tp_order_id={tp_order_id}"
            )

            pair_cancelled = True
            for order_id, label in [(sl_order_id, "SL"), (tp_order_id, "TP")]:
                if not order_id:
                    self.logger.warning(
                        f"⚠️ Missing {label} order ID for {oco_symbol} — "
                        f"skipping cancel, pair may need reconciliation"
                    )
                    pair_cancelled = False
                    all_cancelled = False
                    continue
                try:
                    self.exchange.client._request_futures_api(
                        "delete",
                        "algoOrder",
                        signed=True,
                        force_params=True,
                        data={"symbol": oco_symbol, "algoId": order_id},
                    )
                    self.logger.info(
                        f"✅ Cancelled algo {label} order {order_id} for {oco_symbol}"
                    )
                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to cancel algo {label} order {order_id}: {e}"
                    )
                    pair_cancelled = False
                    all_cancelled = False
            if pair_cancelled:
                self.logger.info(
                    f"✅ OCO PAIR CANCELLED for strategy {oco_info.get('strategy_position_id')}"
                )
                oco_info["status"] = "cancelled"

        return all_cancelled

    async def cancel_other_order(
        self,
        position_id: str,
        filled_order_id: str,
        symbol: str | None = None,
        position_side: str | None = None,
    ) -> tuple[bool, str]:
        """
        Cancel the other order when one SL/TP order is filled (OCO behavior)

        Args:
            position_id: Position identifier
            filled_order_id: ID of the order that was filled
            symbol: Trading symbol (optional, used to build exchange_position_key)
            position_side: Position side LONG/SHORT (optional, used to build exchange_position_key)

        Returns:
            Tuple of (success: bool, close_reason: str)
            close_reason will be 'take_profit', 'stop_loss', or 'unknown'
        """

        # Build exchange_position_key if symbol and position_side provided
        exchange_position_key = None
        if symbol and position_side:
            exchange_position_key = f"{symbol}_{position_side}"

        # Try to find OCO pairs - first by exchange_position_key, then by position_id
        oco_list = None
        oco_info = None
        found_key = None

        # Debug logging
        self.logger.debug(
            f"Looking for OCO pair: position_id={position_id}, filled_order_id={filled_order_id}, "
            f"exchange_position_key={exchange_position_key}, "
            f"active_keys={list(self.active_oco_pairs.keys())}"
        )

        if exchange_position_key and exchange_position_key in self.active_oco_pairs:
            oco_list = self.active_oco_pairs[exchange_position_key]
            self.logger.debug(
                f"Found {len(oco_list)} OCO pair(s) under {exchange_position_key}"
            )
            # Find the OCO pair that matches the filled_order_id
            for oco in oco_list:
                if (
                    oco.get("sl_order_id") == filled_order_id
                    or oco.get("tp_order_id") == filled_order_id
                ):
                    oco_info = oco
                    found_key = exchange_position_key
                    self.logger.debug(f"Matched OCO pair: {oco_info}")
                    break
        elif position_id in self.active_oco_pairs:
            # Backward compatibility - old key structure
            oco_data = self.active_oco_pairs[position_id]
            if isinstance(oco_data, dict):
                oco_info = oco_data
            elif isinstance(oco_data, list) and len(oco_data) > 0:
                # Find matching OCO pair
                for oco in oco_data:
                    if (
                        oco.get("sl_order_id") == filled_order_id
                        or oco.get("tp_order_id") == filled_order_id
                    ):
                        oco_info = oco
                        break
            else:
                oco_info = oco_data[0] if oco_data else None

        # If still not found, search all OCO pairs for matching position_id
        if oco_info is None:
            for key, pairs in self.active_oco_pairs.items():
                if isinstance(pairs, list):
                    for pair in pairs:
                        if pair.get("position_id") == position_id:
                            if (
                                pair.get("sl_order_id") == filled_order_id
                                or pair.get("tp_order_id") == filled_order_id
                            ):
                                oco_info = pair
                                found_key = key
                                break
                    if oco_info:
                        break
                elif (
                    isinstance(pairs, dict) and pairs.get("position_id") == position_id
                ):
                    if (
                        pairs.get("sl_order_id") == filled_order_id
                        or pairs.get("tp_order_id") == filled_order_id
                    ):
                        oco_info = pairs
                        found_key = key
                        break

        if oco_info is None:
            self.logger.warning(f"⚠️  No OCO pair found for position {position_id}")
            return False, "unknown"
        sl_order_id = oco_info["sl_order_id"]
        tp_order_id = oco_info["tp_order_id"]

        # Determine which order to cancel and the close reason
        close_reason = "unknown"
        if filled_order_id == sl_order_id:
            order_to_cancel = tp_order_id
            filled_type = "Stop Loss"
            cancel_type = "Take Profit"
            close_reason = "stop_loss"
        elif filled_order_id == tp_order_id:
            order_to_cancel = sl_order_id
            filled_type = "Take Profit"
            cancel_type = "Stop Loss"
            close_reason = "take_profit"
        else:
            self.logger.warning(
                f"⚠️  Filled order {filled_order_id} not found in OCO pair"
            )
            return False, "unknown"

        self.logger.info(
            f"OCO triggered: position_id={position_id}, filled_type={filled_type}, "
            f"filled_order_id={filled_order_id}, order_to_cancel={order_to_cancel}, "
            f"cancel_type={cancel_type}, close_reason={close_reason}"
        )

        # Guard: if order_to_cancel is None or empty, the paired order is already gone
        # (lost state from pod restart); treat as externally closed — code=-1102 scenario
        if not order_to_cancel:
            self.logger.info(
                f"INFO: Order externally closed (null order_id), cleaning up local state: "
                f"position={position_id}, cancel_type={cancel_type}"
            )
            self._mark_oco_completed(
                found_key, position_id, sl_order_id, tp_order_id, close_reason
            )
            return True, close_reason

        # #532 (H4 of #977): bounded retry with capped exponential backoff around
        # the surviving-leg cancellation. A single transient network blip or a
        # momentary Binance -1xxx used to leave the surviving leg live and the
        # OCO pair tracking dropped — re-creating the orphan-leg incident class.
        # Retry ONLY transient failures; terminal states (-2011 already
        # closed / -2013 does not exist) are treated as an idempotent no-op
        # success and never retried (respects #504 no same-price spin: we do not
        # loop on a leg that is genuinely gone). Setting OCO_CANCEL_RETRY_ATTEMPTS=1
        # restores the pre-#532 single-attempt behaviour (rollback lever).
        max_attempts = max(1, OCO_CANCEL_RETRY_ATTEMPTS)
        last_error_label = "unknown"
        for attempt in range(1, max_attempts + 1):
            try:
                # Cancel the other order. NOTE: futures_cancel_order is the
                # standard-order path used for SL/TP legs here; algo-order
                # cancels go through the algoOrder endpoint elsewhere (#490 /
                # -1102 — do NOT route algo cancels through /order).
                cancel_result = self.exchange.client.futures_cancel_order(
                    symbol=oco_info["symbol"], orderId=order_to_cancel
                )

                if cancel_result:
                    self.logger.info(
                        f"Order cancelled successfully: order_type={cancel_type}, "
                        f"order_id={order_to_cancel} (attempt {attempt}/{max_attempts})"
                    )
                    # Update status in the correct location
                    if found_key and found_key in self.active_oco_pairs:
                        # Find and update the matching OCO pair in the list
                        for oco in self.active_oco_pairs[found_key]:
                            if (
                                oco.get("sl_order_id") == sl_order_id
                                or oco.get("tp_order_id") == tp_order_id
                            ):
                                oco["status"] = "completed"
                                oco["close_reason"] = close_reason
                                break
                    elif position_id in self.active_oco_pairs:
                        # Backward compatibility
                        if isinstance(self.active_oco_pairs[position_id], dict):
                            self.active_oco_pairs[position_id]["status"] = "completed"
                            self.active_oco_pairs[position_id]["close_reason"] = (
                                close_reason
                            )
                        elif isinstance(self.active_oco_pairs[position_id], list):
                            for oco in self.active_oco_pairs[position_id]:
                                if (
                                    oco.get("sl_order_id") == sl_order_id
                                    or oco.get("tp_order_id") == tp_order_id
                                ):
                                    oco["status"] = "completed"
                                    oco["close_reason"] = close_reason
                                    break
                    return True, close_reason

                # Falsy result from the client — transient (empty response /
                # ambiguous). Retry within budget.
                last_error_label = "empty_response"
                self.logger.error(
                    f"❌ FAILED TO CANCEL {cancel_type} ORDER "
                    f"(attempt {attempt}/{max_attempts}, empty response)"
                )

            except Exception as e:
                # Terminal: order already cancelled/filled or unknown. Idempotent
                # no-op — a retry after the leg actually cancelled must NOT error.
                if "code=-2011" in str(e) or "Unknown order sent" in str(e):
                    self.logger.warning(
                        f"⚠️ {cancel_type} order already closed or unknown "
                        f"(likely filled/cancelled): {e}"
                    )
                    self._mark_oco_completed(
                        found_key, position_id, sl_order_id, tp_order_id, close_reason
                    )
                    return True, close_reason

                # Terminal: ghost order filled/cancelled externally (e.g. after
                # pod restart). Also idempotent no-op success.
                if "code=-2013" in str(e) or "Order does not exist" in str(e):
                    self.logger.info(
                        f"INFO: Order externally closed, cleaning up local state: "
                        f"position={position_id}, cancel_type={cancel_type}, error={e}"
                    )
                    self._mark_oco_completed(
                        found_key, position_id, sl_order_id, tp_order_id, close_reason
                    )
                    return True, close_reason

                # Transient (network blip, momentary -1xxx, timeout). Retry.
                last_error_label = type(e).__name__
                self.logger.error(
                    f"❌ ERROR CANCELLING {cancel_type} ORDER "
                    f"(attempt {attempt}/{max_attempts}): {e}"
                )

            # Backoff before the next attempt (skip the sleep after the final try).
            if attempt < max_attempts:
                delay = min(
                    OCO_CANCEL_RETRY_BASE_DELAY
                    * (OCO_CANCEL_RETRY_BACKOFF_MULTIPLIER ** (attempt - 1)),
                    OCO_CANCEL_RETRY_MAX_DELAY,
                )
                await asyncio.sleep(delay)

        # Retry budget exhausted. Do NOT drop the pair — keep it tracked so the
        # next monitor poll retries and the orphan is observable. Increment the
        # failure metric and fire a NATS alert (both best-effort; the alert path
        # must never break the caller).
        self.logger.error(
            f"❌ OCO surviving-leg cancel EXHAUSTED after {max_attempts} attempts: "
            f"order_type={cancel_type}, order_id={order_to_cancel}, "
            f"symbol={oco_info['symbol']}, last_error={last_error_label}. "
            f"Pair remains tracked for the next poll."
        )
        try:
            oco_cancel_retry_exhausted_total.labels(
                symbol=oco_info["symbol"],
                reason=last_error_label,
            ).inc()
        except Exception:
            self.logger.debug(
                "oco_cancel_retry_exhausted metric inc failed", exc_info=True
            )
        try:
            await alert_publisher.publish(
                alert_name=f"oco_cancel_retry_exhausted.{oco_info['symbol']}",
                severity="critical",
                payload={
                    "symbol": oco_info["symbol"],
                    "position_side": position_side,
                    "position_id": position_id,
                    "cancel_type": cancel_type,
                    "order_to_cancel": order_to_cancel,
                    "attempts": max_attempts,
                    "last_error": last_error_label,
                    "close_reason": close_reason,
                },
            )
        except Exception:
            self.logger.debug(
                "oco_cancel_retry_exhausted alert publish failed", exc_info=True
            )
        return False, close_reason

    def _mark_oco_completed(
        self,
        found_key: str | None,
        position_id: str,
        sl_order_id: str | None,
        tp_order_id: str | None,
        close_reason: str,
    ) -> None:
        """Mark an OCO pair as completed in local state (used for ghost-order cleanup)."""
        if found_key and found_key in self.active_oco_pairs:
            for oco in self.active_oco_pairs[found_key]:
                if (
                    oco.get("sl_order_id") == sl_order_id
                    or oco.get("tp_order_id") == tp_order_id
                ):
                    oco["status"] = "externally_closed"
                    oco["close_reason"] = close_reason
                    break
        elif position_id in self.active_oco_pairs:
            oco_data = self.active_oco_pairs[position_id]
            items = [oco_data] if isinstance(oco_data, dict) else oco_data
            for oco in items:
                if (
                    oco.get("sl_order_id") == sl_order_id
                    or oco.get("tp_order_id") == tp_order_id
                ):
                    oco["status"] = "externally_closed"
                    oco["close_reason"] = close_reason
                    break

    async def reconcile_from_exchange(self) -> int:
        """Rebuild active_oco_pairs from live Binance open orders on startup.

        Queries open STOP_MARKET (SL) and TAKE_PROFIT_MARKET (TP) orders from Binance,
        groups them by symbol+positionSide, and populates active_oco_pairs so that
        OCO monitoring can resume correctly after a pod restart.

        Returns:
            Number of OCO pairs rebuilt.
        """
        if (
            not self.exchange
            or not hasattr(self.exchange, "client")
            or self.exchange.client is None
        ):
            self.logger.warning(
                "[STARTUP] Cannot reconcile OCO pairs: exchange not ready"
            )
            return 0

        # SL/TP orders are placed via Binance Algo Order API (algoType=CONDITIONAL),
        # so they appear in openAlgoOrders (algoId), not futures_get_open_orders.
        # Testnet may not support openAlgoOrders — fall back to standard open orders.
        open_orders: list[dict[str, Any]] = []
        try:
            open_orders = await self.exchange.get_open_algo_orders()
            self.logger.info(
                f"[STARTUP] Fetched {len(open_orders)} algo orders for OCO reconciliation"
            )
        except Exception as e:
            self.logger.warning(
                f"[STARTUP] Algo orders API unavailable ({e}), falling back to standard open orders"
            )

        if not open_orders:
            try:
                # #465: offload sync REST to a thread so the event loop stays
                # responsive for the boot probe scheduled immediately after.
                std_orders = await asyncio.to_thread(
                    self.exchange.client.futures_get_open_orders
                )
                open_orders = list(std_orders) if std_orders else []
                self.logger.info(
                    f"[STARTUP] Fallback: fetched {len(open_orders)} standard open orders for OCO reconciliation"
                )
            except Exception as e2:
                self.logger.warning(
                    f"[STARTUP] Failed to fetch standard open orders for OCO reconciliation: {e2}"
                )
                return 0

        sl_orders: dict[
            str, list[dict[str, Any]]
        ] = {}  # key: "SYMBOL_SIDE" -> list of orders
        tp_orders: dict[str, list[dict[str, Any]]] = {}

        for o in open_orders:
            order_type = o.get("type", o.get("orderType", ""))
            if order_type == "CONDITIONAL":
                order_type = o.get("origType", order_type)
            position_side = o.get("positionSide", "BOTH")
            symbol = o.get("symbol", "")
            key = f"{symbol}_{position_side}"

            if order_type in ("STOP_MARKET", "STOP"):
                sl_orders.setdefault(key, []).append(o)
            elif order_type in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                tp_orders.setdefault(key, []).append(o)

        rebuilt = 0
        all_keys = set(sl_orders.keys()) | set(tp_orders.keys())
        for key in all_keys:
            sl_list = sorted(
                sl_orders.get(key, []),
                key=lambda x: x.get("createTime", x.get("time", 0)),
            )
            tp_list = sorted(
                tp_orders.get(key, []),
                key=lambda x: x.get("createTime", x.get("time", 0)),
            )
            parts = key.split("_", 1)
            sym = parts[0]
            pos_side = parts[1] if len(parts) > 1 else "BOTH"

            # AC-4 (#352): track every order individually — zip() silently drops unmatched
            # orders (shorter list wins), leaving orphaned SL or TP with no monitoring.
            # Instead, pair what we can, then register remaining orders as solo entries so
            # the monitoring loop can cancel them if the position has already closed.
            paired_sl = set()
            paired_tp = set()
            for sl_o, tp_o in zip(sl_list, tp_list, strict=False):
                sl_id = str(sl_o.get("algoId") or sl_o.get("orderId", ""))
                tp_id = str(tp_o.get("algoId") or tp_o.get("orderId", ""))
                oco_info = {
                    "position_id": f"reconciled_{sym}_{pos_side}_{int(time.time())}",
                    "strategy_position_id": None,
                    "entry_price": 0.0,
                    "quantity": float(sl_o.get("quantity", sl_o.get("origQty", 0))),
                    "sl_order_id": sl_id,
                    "tp_order_id": tp_id,
                    "symbol": sym,
                    "position_side": pos_side,
                    "status": "active",
                    "created_at": time.time(),
                    "reconciled": True,
                }
                self.active_oco_pairs.setdefault(key, []).append(oco_info)
                paired_sl.add(sl_id)
                paired_tp.add(tp_id)
                rebuilt += 1

            # Register unpaired SL orders so monitoring can cancel them
            for sl_o in sl_list:
                sl_id = str(sl_o.get("algoId") or sl_o.get("orderId", ""))
                if sl_id in paired_sl:
                    continue
                oco_info = {
                    "position_id": f"reconciled_orphan_sl_{sym}_{pos_side}_{int(time.time())}",
                    "strategy_position_id": None,
                    "entry_price": 0.0,
                    "quantity": float(sl_o.get("quantity", sl_o.get("origQty", 0))),
                    "sl_order_id": sl_id,
                    "tp_order_id": None,
                    # #490: record algo-ness at scan time so the cancel path
                    # routes by source (/openAlgoOrders) not by id length.
                    "sl_is_algo": "algoId" in sl_o,
                    "symbol": sym,
                    "position_side": pos_side,
                    "status": "active",
                    "created_at": time.time(),
                    "reconciled": True,
                    "orphaned": True,
                }
                self.active_oco_pairs.setdefault(key, []).append(oco_info)
                rebuilt += 1
                self.logger.warning(
                    f"[STARTUP] Registered orphaned SL order {sl_id} for {key} (no matching TP)"
                )

            # Register unpaired TP orders so monitoring can cancel them
            for tp_o in tp_list:
                tp_id = str(tp_o.get("algoId") or tp_o.get("orderId", ""))
                if tp_id in paired_tp:
                    continue
                oco_info = {
                    "position_id": f"reconciled_orphan_tp_{sym}_{pos_side}_{int(time.time())}",
                    "strategy_position_id": None,
                    "entry_price": 0.0,
                    "quantity": float(tp_o.get("quantity", tp_o.get("origQty", 0))),
                    "sl_order_id": None,
                    "tp_order_id": tp_id,
                    # #490: record algo-ness at scan time so the cancel path
                    # routes by source (/openAlgoOrders) not by id length.
                    "tp_is_algo": "algoId" in tp_o,
                    "symbol": sym,
                    "position_side": pos_side,
                    "status": "active",
                    "created_at": time.time(),
                    "reconciled": True,
                    "orphaned": True,
                }
                self.active_oco_pairs.setdefault(key, []).append(oco_info)
                rebuilt += 1
                self.logger.warning(
                    f"[STARTUP] Registered orphaned TP order {tp_id} for {key} (no matching SL)"
                )

        self.logger.info(f"[STARTUP] Rebuilt {rebuilt} active OCO pairs from Binance")

        # AC (#550): emit active_oco_pairs_per_position for every reconciled
        # (symbol, position_side) so the gauge reflects exchange truth after a
        # restart instead of silently undercounting. Without this, the gauge is
        # only ever set on live placement (place_oco_orders success path), so a
        # freshly-restarted pod that reconciled N covered positions from Binance
        # would report zero rows until the next placement — the "under-track
        # (standing)" symptom in #550.
        self._sync_oco_pairs_gauge()

        # Start monitoring for any reconciled pairs so they are tracked going forward
        if rebuilt > 0 and not self.monitoring_active:
            await self.start_monitoring()
            self.logger.info("[STARTUP] OCO monitoring started for reconciled pairs")

        return rebuilt

    async def start_monitoring(self) -> None:
        """Start monitoring active orders for fills and trigger OCO logic"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        self.monitoring_task = asyncio.create_task(self._monitor_orders())
        self.logger.info("🔍 STARTED ORDER MONITORING")

    async def stop_monitoring(self) -> None:
        """Stop monitoring orders"""
        self.monitoring_active = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        self.logger.info("🔍 STOPPED ORDER MONITORING")

    def _is_conditional_pending_entry(
        self, order: TradeOrder, result: dict[str, Any] | None
    ) -> bool:
        """Return True when entry is a CONDITIONAL/algo order that hasn't fired yet.

        CONDITIONAL_LIMIT/CONDITIONAL_STOP entries return status=NEW with no real fill.
        Placing OCO against the stale target_price triggers Binance -2021 (#371).
        """
        order_type = str(order.type)
        if order_type not in (
            OrderType.CONDITIONAL_LIMIT.value,
            OrderType.CONDITIONAL_STOP.value,
        ):
            return False
        status = (result or {}).get("status")
        return status == "NEW"

    async def defer_oco_until_filled(
        self, order: TradeOrder, entry_order_id: str
    ) -> None:
        """Queue a CONDITIONAL entry for post-fill OCO placement.

        The monitor loop polls the entry order; on FILLED it captures avgPrice
        from the exchange and triggers OCO with the real entry price.
        """
        if not entry_order_id:
            self.logger.warning(
                f"⚠️ Cannot defer OCO for {order.symbol}: missing entry_order_id"
            )
            return
        self.pending_entries[entry_order_id] = {
            "order": order,
            "symbol": order.symbol,
            "entry_order_id": entry_order_id,
            "registered_at": time.time(),
        }
        self.logger.info(
            f"⏸️ OCO DEFERRED: entry {entry_order_id} ({order.symbol}) is CONDITIONAL/NEW — "
            f"waiting for FILLED before placing SL/TP. pending_count={len(self.pending_entries)}"
        )
        if not self.monitoring_active:
            await self.start_monitoring()

    async def _check_pending_entries(self) -> None:
        """Poll deferred entries; trigger OCO when their entry order is FILLED.

        Note: partially_filled is intentionally NOT terminal here. A CONDITIONAL
        order can fill in stages; placing OCO sized to the partial fill would
        leave the residual fill uncovered. We wait for fully FILLED so the OCO
        quantity matches the complete entry size.
        """
        if not self.pending_entries:
            return
        filled_terminal = {"filled", "FILLED"}
        for entry_id, info in list(self.pending_entries.items()):
            order = info["order"]
            symbol = info["symbol"]
            try:
                status_resp = await self.exchange.get_order_status(symbol, entry_id)
            except Exception as e:
                self.logger.warning(
                    f"⚠️ Pending-entry status check failed for {entry_id} ({symbol}): {e}"
                )
                continue
            status = status_resp.get("status")
            if status not in filled_terminal:
                continue

            try:
                executed_qty = float(status_resp.get("executed_qty") or 0)
                cum_quote = float(status_resp.get("cummulative_quote_qty") or 0)
            except (ValueError, TypeError):
                executed_qty = 0.0
                cum_quote = 0.0

            if executed_qty > 0 and cum_quote > 0:
                avg_price = cum_quote / executed_qty
            else:
                try:
                    avg_price = float(status_resp.get("price") or 0)
                except (ValueError, TypeError):
                    avg_price = 0.0

            if avg_price <= 0 or executed_qty <= 0:
                self.logger.warning(
                    f"⚠️ Cannot place deferred OCO for {symbol}: avg_price={avg_price}, "
                    f"executed_qty={executed_qty}. Dropping pending entry {entry_id}."
                )
                del self.pending_entries[entry_id]
                continue

            synthetic_result = {
                "order_id": entry_id,
                "status": "filled",
                "fill_price": avg_price,
                "amount": executed_qty,
                "symbol": symbol,
            }
            self.logger.info(
                f"▶️ Deferred OCO trigger: entry {entry_id} FILLED at avg_price={avg_price} "
                f"qty={executed_qty}. Placing OCO now."
            )
            # Remove BEFORE awaiting OCO placement so a re-entrant loop tick can't double-trigger.
            del self.pending_entries[entry_id]
            try:
                if self.dispatcher:
                    await self.dispatcher._place_risk_management_orders(
                        order, synthetic_result
                    )
            except Exception as e:
                self.logger.error(
                    f"❌ Deferred OCO placement failed for {symbol} (entry {entry_id}): {e}"
                )

    async def _cancel_orphaned_order(
        self, symbol: str, order_id: str, is_algo: bool
    ) -> None:
        """Cancel a single orphaned reconciled order, routing algo vs standard.

        Per #490: orphaned closePosition TP/SL orders discovered at startup are
        algo orders (disjoint from ``/openOrders``, #483) and MUST be cancelled
        via ``cancel_algo_order`` (``algoId``) — never ``cancel_order``
        (``orderId``), which returns ``APIError(-1102)``. Whether the order is an
        algo order is recorded at scan time (``sl_is_algo``/``tp_is_algo``); we
        never re-classify by id length. A ``-4029`` ("order does not exist")
        means it was already cancelled out-of-band — INFO, not ERROR (AC6).
        """
        from tradeengine.metrics import orphan_algo_cancel_total

        try:
            if is_algo:
                await self.exchange.cancel_algo_order(symbol, order_id)
            else:
                # NOTE: cancel_order signature is (symbol, order_id). The legacy
                # call here passed them swapped, which is what actually produced
                # the -1102 in production (orderId received a symbol string).
                await self.exchange.cancel_order(symbol, order_id)
            self.logger.info(f"🗑️  Cancelled orphaned order {order_id} for {symbol}")
            if is_algo:
                orphan_algo_cancel_total.labels(
                    outcome="succeeded", symbol=symbol
                ).inc()
        except Exception as cancel_err:
            code = getattr(cancel_err, "code", None)
            if is_algo and code == -4029:
                self.logger.info(
                    f"Orphaned algo order {order_id} for {symbol} already "
                    f"cancelled (-4029); nothing to do"
                )
                orphan_algo_cancel_total.labels(
                    outcome="not_found_4029", symbol=symbol
                ).inc()
            else:
                self.logger.warning(
                    f"Failed to cancel orphaned order {order_id}: {cancel_err}"
                )
                if is_algo:
                    orphan_algo_cancel_total.labels(
                        outcome="failed_other", symbol=symbol
                    ).inc()

    @staticmethod
    def _coerce_created_at(value: Any, fallback: float) -> float:
        """#972: return an epoch-seconds float for an OCO pair's created_at.

        Production code stores ``time.time()`` (a float), but tests and any
        future serialization path may present an int or an ISO-8601 string.
        Anything unparseable falls back to ``fallback`` (yielding age ~0)
        rather than raising and aborting the monitor cycle.
        """
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                # Accept trailing 'Z' as UTC.
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                return fallback
        return fallback

    def _remove_oco_pair_age_series(self, symbol: str, position_side: str) -> None:
        """#972: drop the oco_pair_age_seconds series for a completed pair.

        prometheus_client raises KeyError if the (symbol, position_side) label
        set was never observed; that is benign here (the series may have already
        been removed on a prior cycle) so we swallow it. The OTel synchronous
        gauge has no per-series removal API — it simply stops receiving points
        for a label set, which the SDK ages out — so nothing to do there.
        """
        try:
            oco_pair_age_seconds.remove(symbol, position_side)
        except KeyError:
            pass

    async def _monitor_orders(self) -> None:
        """
        Monitor active orders for fills and trigger OCO logic
        This runs in a separate task
        NEW: Works with multiple OCO pairs per exchange position
        NEW (#371): Also drives deferred-OCO post-fill triggers via _check_pending_entries
        """

        self.logger.info("🔍 STARTING ORDER MONITORING (MULTI-STRATEGY MODE)")

        # Count total OCO pairs across all exchange positions
        total_pairs = sum(len(pairs) for pairs in self.active_oco_pairs.values())
        self.logger.info(
            f"Active OCO pairs: {total_pairs} across {len(self.active_oco_pairs)} positions"
        )

        while self.monitoring_active and (
            self.active_oco_pairs or self.pending_entries
        ):
            try:
                # #371: Drive deferred-OCO triggers for CONDITIONAL entries that have FILLED.
                await self._check_pending_entries()

                # Check each exchange position's OCO pairs
                for exchange_position_key, oco_list in list(
                    self.active_oco_pairs.items()
                ):
                    if not oco_list:
                        continue

                    # Get symbol for batch query
                    symbol = oco_list[0]["symbol"] if oco_list else None
                    if not symbol:
                        continue

                    # #972: publish OCO pair age (seconds since the pair entered
                    # active_oco_pairs). Set every cycle for each active pair so a
                    # stuck/stale pair (>300s) is visible in Grafana. Dual-export
                    # (prometheus + OTel) per the #415/#497 pattern. Guarded so a
                    # malformed created_at can never abort the monitor cycle.
                    _now = time.time()
                    for _oco in oco_list:
                        if _oco.get("status") != "active":
                            continue
                        try:
                            _created = self._coerce_created_at(
                                _oco.get("created_at"), _now
                            )
                            _age = _now - _created
                            oco_pair_age_seconds.labels(
                                symbol=_oco["symbol"],
                                position_side=_oco["position_side"],
                            ).set(_age)
                            otel_oco_pair_age_seconds.set(
                                _age,
                                {
                                    "symbol": _oco["symbol"],
                                    "position_side": _oco["position_side"],
                                },
                            )
                        except Exception as _age_err:  # pragma: no cover - defensive
                            self.logger.debug(
                                "oco_pair_age_seconds update skipped for "
                                f"{_oco.get('symbol')} {_oco.get('position_side')}: {_age_err}"
                            )

                    # Query all open orders for this symbol once
                    # Use the robust combined list (Standard + Algo) to avoid ghost orders
                    open_order_ids = await self.exchange.get_all_open_orders(
                        symbol=symbol
                    )

                    # Check each OCO pair in this position
                    for oco_info in oco_list:
                        if oco_info["status"] != "active":
                            continue

                        sl_order_id = oco_info["sl_order_id"]
                        tp_order_id = oco_info["tp_order_id"]

                        # AC-4 (#352): orphaned entries have one side set to None.
                        # Cancel whichever order still exists and mark completed.
                        if oco_info.get("orphaned"):
                            live_id = sl_order_id or tp_order_id
                            # #490: route by the side that actually carries the
                            # live order, using the algo-ness recorded at scan
                            # time — never re-classify by id length.
                            live_is_algo = (
                                oco_info.get("sl_is_algo")
                                if sl_order_id
                                else oco_info.get("tp_is_algo")
                            )
                            if live_id and live_id in open_order_ids:
                                await self._cancel_orphaned_order(
                                    symbol, live_id, bool(live_is_algo)
                                )
                            oco_info["status"] = "completed"
                            continue

                        # Check if orders still exist
                        sl_exists = sl_order_id in open_order_ids
                        tp_exists = tp_order_id in open_order_ids

                        # Determine which order filled
                        filled_order_id = None
                        close_reason = "unknown"

                        if not sl_exists and tp_exists:
                            # Stop loss filled
                            filled_order_id = sl_order_id
                            close_reason = "stop_loss"
                            self.logger.info(
                                f"🔴 SL TRIGGERED for strategy {oco_info.get('strategy_position_id')}"
                            )
                        elif sl_exists and not tp_exists:
                            # Take profit filled
                            filled_order_id = tp_order_id
                            close_reason = "take_profit"
                            self.logger.info(
                                f"🟢 TP TRIGGERED for strategy {oco_info.get('strategy_position_id')}"
                            )
                        elif not sl_exists and not tp_exists:
                            # Both gone - OCO completed
                            self.logger.info(
                                f"✅ OCO completed for strategy {oco_info.get('strategy_position_id')}"
                            )
                            oco_info["status"] = "completed"
                            continue

                        # If an order filled, cancel the other order and close the strategy position
                        if filled_order_id:
                            try:
                                # Cancel the other order (OCO behavior)
                                # Note: position_id is legacy parameter (not used by _close_position_on_oco_completion)
                                # but kept for consistency with function signature
                                position_id = oco_info.get("position_id", "")
                                (
                                    cancel_success,
                                    cancel_reason,
                                ) = await self.cancel_other_order(
                                    position_id=position_id,
                                    filled_order_id=filled_order_id,
                                    symbol=oco_info["symbol"],
                                    position_side=oco_info["position_side"],
                                )

                                if cancel_success:
                                    self.logger.info(
                                        f"✅ OCO cancellation successful: {cancel_reason}"
                                    )
                                else:
                                    # Handle cancellation failure cases
                                    # If cancellation fails because order already filled, this indicates
                                    # a race condition where both SL and TP triggered simultaneously
                                    # If cancellation fails for other reasons, log warning but proceed
                                    # with position close to avoid leaving orphaned positions
                                    if (
                                        "already filled" in cancel_reason.lower()
                                        or "not found" in cancel_reason.lower()
                                    ):
                                        self.logger.warning(
                                            f"⚠️  OCO cancellation failed (order may already be filled): {cancel_reason}"
                                        )
                                    else:
                                        self.logger.warning(
                                            f"⚠️  OCO cancellation failed: {cancel_reason}. Proceeding with position close."
                                        )

                                # Close position with strategy attribution
                                # Note: We proceed with position close even if cancellation failed to avoid
                                # leaving orphaned positions. The exchange will handle any remaining orders.
                                await self._close_position_on_oco_completion(
                                    position_id=position_id,  # Legacy parameter, not used by function
                                    filled_order_id=filled_order_id,
                                    close_reason=close_reason,
                                    oco_info=oco_info,
                                    dispatcher=self.dispatcher,
                                )
                            except Exception as e:
                                self.logger.error(
                                    f"❌ Failed to process OCO completion: {e}"
                                )

                # Clean up completed OCO pairs
                for exchange_position_key in list(self.active_oco_pairs.keys()):
                    # #972: drop the age gauge series for pairs leaving the active
                    # set (completed/cancelled/externally-closed) so they don't
                    # report a frozen age forever. Remove the (symbol,
                    # position_side) series when no active pair remains for it.
                    active_pairs = [
                        oco
                        for oco in self.active_oco_pairs[exchange_position_key]
                        if oco["status"] == "active"
                    ]
                    _active_label_pairs = {
                        (oco["symbol"], oco["position_side"]) for oco in active_pairs
                    }
                    for _oco in self.active_oco_pairs[exchange_position_key]:
                        _label_pair = (_oco["symbol"], _oco["position_side"])
                        if _oco["status"] != "active" and (
                            _label_pair not in _active_label_pairs
                        ):
                            self._remove_oco_pair_age_series(*_label_pair)

                    if active_pairs:
                        self.active_oco_pairs[exchange_position_key] = active_pairs
                    else:
                        # No active pairs left, remove the key
                        del self.active_oco_pairs[exchange_position_key]

                # Wait before next check. #534: when the WS-wake flag is on,
                # sleep is interruptible — a FILLED SL/TP leg observed on the
                # user-data stream sets _oco_wake_event and we re-poll at once
                # (bounded by the same 2s ceiling as a backstop). When off,
                # behaviour is identical to the original fixed 2s poll.
                if settings.te_oco_ws_wake_enabled:
                    try:
                        await asyncio.wait_for(self._oco_wake_event.wait(), timeout=2)
                    except TimeoutError:
                        pass
                    finally:
                        self._oco_wake_event.clear()
                else:
                    await asyncio.sleep(2)  # Check every 2 seconds

            except Exception as e:
                self.logger.error(f"❌ ERROR IN ORDER MONITORING: {e}")
                await asyncio.sleep(5)  # Wait longer on error

        self.logger.info("🔍 ORDER MONITORING STOPPED")

    async def _close_position_on_oco_completion(
        self,
        position_id: str,
        filled_order_id: str,
        close_reason: str,
        oco_info: dict[str, Any],
        dispatcher,
    ) -> None:
        """Close ONLY the owning strategy's position when its OCO completes

        Args:
            position_id: Position identifier (legacy, not used with new structure)
            filled_order_id: The order ID that was filled (SL or TP)
            close_reason: 'take_profit' or 'stop_loss'
            oco_info: OCO pair information with strategy context
            dispatcher: Dispatcher instance
        """
        try:
            symbol = oco_info["symbol"]
            position_side = oco_info["position_side"]
            exchange_position_key = f"{symbol}_{position_side}"

            self.logger.info("🎯 STRATEGY OCO TRIGGERED - CLOSING OWNING STRATEGY ONLY")
            self.logger.info(f"  Symbol: {symbol}")
            self.logger.info(f"  Position Side: {position_side}")
            self.logger.info(f"  Close Reason: {close_reason}")
            self.logger.info(f"  Filled Order ID: {filled_order_id}")

            # NEW: Find which strategy's OCO filled
            owning_oco = oco_info  # This is already the owning OCO from _monitor_orders
            strategy_position_id = owning_oco.get("strategy_position_id")

            # Use robust float conversion for all numeric fields from stored state
            try:
                raw_entry_price = owning_oco.get("entry_price")
                entry_price = (
                    float(raw_entry_price) if raw_entry_price is not None else 0.0
                )
            except (ValueError, TypeError):
                self.logger.warning(
                    f"⚠️ Invalid entry_price in OCO info: {owning_oco.get('entry_price')}"
                )
                entry_price = 0.0

            try:
                raw_quantity = owning_oco.get("quantity")
                exit_quantity = float(raw_quantity) if raw_quantity is not None else 0.0
            except (ValueError, TypeError):
                exit_quantity = 0.0

            if not strategy_position_id:
                self.logger.error(
                    f"❌ No strategy_position_id in OCO info for {filled_order_id}"
                )
                return

            self.logger.info(f"  🎯 Owning Strategy Position: {strategy_position_id}")
            self.logger.info(f"  📍 Entry Price: ${entry_price:,.2f}")
            self.logger.info(f"  📊 Quantity: {exit_quantity}")

            # Step 1: Fetch filled order details from Binance
            try:
                order_details = self.exchange.client.futures_get_order(
                    symbol=symbol, orderId=filled_order_id
                )

                # Extract exit data
                exit_price = float(order_details.get("avgPrice", 0))
                filled_quantity = float(order_details.get("executedQty", 0))

                self.logger.info(f"  📤 Exit Price: ${exit_price:,.2f}")
                self.logger.info(f"  📤 Filled Quantity: {filled_quantity}")

            except Exception as e:
                self.logger.error(f"❌ Failed to fetch order details: {e}")
                # Use strategy's quantity from OCO info
                exit_price = entry_price  # Fallback
                filled_quantity = exit_quantity
                self.logger.warning("Using fallback values")

            # Step 2: Calculate P&L using THIS strategy's entry price
            if position_side == "LONG":
                pnl = (exit_price - entry_price) * filled_quantity
            else:  # SHORT
                pnl = (entry_price - exit_price) * filled_quantity

            pnl_pct = (
                (pnl / (entry_price * filled_quantity) * 100)
                if entry_price > 0 and filled_quantity > 0
                else 0.0
            )

            self.logger.info("  💰 P&L Calculation:")
            self.logger.info(f"     Entry: ${entry_price:,.2f}")
            self.logger.info(f"     Exit: ${exit_price:,.2f}")
            self.logger.info(f"     Quantity: {filled_quantity}")
            self.logger.info(f"     Gross P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)")

            # Step 3: Close ONLY this strategy's position
            from tradeengine.strategy_position_manager import strategy_position_manager

            # Get strategy_id from position manager
            strategy_id = "unknown"
            if strategy_position_manager:
                # Get the position to extract strategy_id
                strategy_pos = strategy_position_manager.get_strategy_position(
                    strategy_position_id
                )
                if strategy_pos:
                    strategy_id = strategy_pos.get("strategy_id", "unknown")

                closure = await strategy_position_manager.close_strategy_position(
                    strategy_position_id=strategy_position_id,
                    exit_price=exit_price,
                    exit_quantity=filled_quantity,
                    close_reason=close_reason,
                    exit_order_id=filled_order_id,
                )

                self.logger.info(
                    f"✅ Strategy position {strategy_position_id} ({strategy_id}) closed: {close_reason}, P&L: ${pnl:,.2f}"
                )

                # #531: publish a `filled` execution event for the OCO exit.
                # This is the only place with the real exit fill (price, qty,
                # PnL) fetched from futures_get_order. Best-effort: a NATS or
                # publisher failure must never propagate into the close path.
                await self._emit_oco_exit_filled_event(
                    closure=closure or {},
                    strategy_id=strategy_id,
                    symbol=symbol,
                    exit_price=exit_price,
                    filled_quantity=filled_quantity,
                    pnl=pnl,
                    filled_order_id=filled_order_id,
                    close_reason=close_reason,
                )

            # Step 4: Cancel the paired order (TP if SL filled, SL if TP filled)
            other_order_id = (
                owning_oco["sl_order_id"]
                if filled_order_id == owning_oco["tp_order_id"]
                else owning_oco["tp_order_id"]
            )

            try:
                # #490: cancel_order signature is (symbol, order_id) — the args
                # were swapped here, the same defect that produced -1102 on the
                # orphan path. Algo orders fall through to the algo-cancel
                # fallback inside cancel_order (triggered on -2011/-4132).
                await self.exchange.cancel_order(symbol, other_order_id)
                self.logger.info(f"✅ Cancelled paired order: {other_order_id}")
            except Exception as e:
                self.logger.warning(
                    f"⚠️ Failed to cancel paired order {other_order_id}: {e}"
                )

            # Step 5: Mark this OCO as completed
            owning_oco["status"] = "completed"

            # Step 6: Export Prometheus metrics
            from tradeengine.metrics import (
                active_oco_pairs_per_position,
                strategy_pnl_realized,
                strategy_sl_triggered_total,
                strategy_tp_triggered_total,
            )

            if close_reason == "take_profit":
                strategy_tp_triggered_total.labels(
                    strategy_id=strategy_id, symbol=symbol, exchange="binance"
                ).inc()
            elif close_reason == "stop_loss":
                strategy_sl_triggered_total.labels(
                    strategy_id=strategy_id, symbol=symbol, exchange="binance"
                ).inc()

            strategy_pnl_realized.labels(
                strategy_id=strategy_id, close_reason=close_reason, exchange="binance"
            ).observe(pnl)

            # Update active OCO pairs gauge
            if exchange_position_key in self.active_oco_pairs:
                active_count = len(
                    [
                        o
                        for o in self.active_oco_pairs[exchange_position_key]
                        if o["status"] == "active"
                    ]
                )
                active_oco_pairs_per_position.labels(
                    symbol=symbol, position_side=position_side, exchange="binance"
                ).set(active_count)

            self.logger.info(
                f"✅ STRATEGY {strategy_position_id} CLOSED: {close_reason}, P&L: ${pnl:,.2f}"
            )

        except Exception as e:
            self.logger.error(
                f"❌ Error closing position {position_id}: {e}", exc_info=True
            )
            raise

    async def _emit_oco_exit_filled_event(
        self,
        *,
        closure: dict[str, Any],
        strategy_id: str,
        symbol: str,
        exit_price: float,
        filled_quantity: float,
        pnl: float,
        filled_order_id: str,
        close_reason: str,
    ) -> None:
        """Publish a `filled` execution event for a completed OCO exit (#531).

        The OCO close path is one of the two async producers that actually
        know a fill happened (the other being the user-data stream). Prior to
        #531 it computed exit price/qty/PnL and emitted Prometheus metrics but
        never published an execution event, so ``execution_events`` recorded
        zero fills in production.

        Best-effort: any failure is swallowed. A NATS or publisher error must
        never propagate into the position-close path.
        """
        try:
            decision_id = closure.get("decision_id")
            if not decision_id:
                # The data-manager consumer drops events with an empty
                # decision_id (execution_event.py:88). Without it the event
                # would be silently discarded; log so the gap is visible.
                self.logger.warning(
                    "#531: OCO exit for %s (%s) has no decision_id on the "
                    "strategy position — `filled` event will be dropped by the "
                    "data-manager consumer. Ensure decision_id is persisted at "
                    "position open.",
                    closure.get("strategy_position_id"),
                    strategy_id,
                )
            await execution_event_publisher.publish(
                event_type="filled",
                strategy_id=strategy_id,
                order_id=str(filled_order_id or ""),
                reason=f"oco_exit_{close_reason}",
                decision_id=decision_id,
                extra={
                    "symbol": symbol,
                    "side": closure.get("side"),
                    "fill_price": exit_price,
                    "price": exit_price,
                    "fill_quantity": filled_quantity,
                    "fill_qty": filled_quantity,
                    "pnl": pnl,
                    "close_reason": close_reason,
                },
            )
        except Exception as emit_err:
            self.logger.warning(
                "#531: failed to publish OCO-exit `filled` event for %s: %s",
                strategy_id,
                emit_err,
            )


class Dispatcher:
    """Central dispatcher for trading operations with distributed state management"""

    def __init__(self, exchange: Any = None) -> None:
        self.settings = Settings()
        self.order_manager = OrderManager()
        self.position_manager = PositionManager(exchange=exchange)
        self.signal_aggregator = SignalAggregator()
        self.exchange = exchange
        self.logger = get_logger(__name__)

        # Initialize OCO Manager for SL/TP order management
        self.oco_manager = OCOManager(exchange, self.logger, self)

        # Initialize Leverage Bound Guard (FR64, P6.4)
        self.leverage_bound_guard = LeverageBoundGuard()

        # Duplicate signal detection cache
        # Format: {signal_id: timestamp} - stores signal IDs with their reception time
        self.signal_cache: dict[str, float] = {}
        self.signal_cache_ttl = (
            10  # Cache TTL in seconds (signals older than this are considered unique)
        )
        self.signal_cache_cleanup_interval = 300  # Cleanup every 5 minutes

        # NEW: Accumulation cooldown tracking
        # Format: {(symbol, side): timestamp} - tracks last accumulation time per position
        self.last_accumulation_time: dict[tuple[str, str], float] = {}
        self.last_cache_cleanup = time.time()

        # Signal to order mapping for strategy position tracking
        # Format: {order_id: Signal} - stores signals for strategy position creation
        self.order_to_signal: dict[str, Signal] = {}

        # NEW: Order to strategy position mapping for OCO attribution
        # Format: {order_id: strategy_position_id} - maps orders to their strategy positions
        self.order_to_strategy_position: dict[str, str] = {}

        self.user_data_consumer: UserDataStreamConsumer | None = None
        # #480 — periodic ghost-position eviction for the strategy-layer
        # tracker.  Started after user_data_consumer wires the truth store.
        self.strategy_position_reconciler: StrategyPositionReconciler | None = None

        # #481 AC5 — open/close thrash circuit-breaker. Per-symbol sliding
        # window that blocks un-audited close emissions once they exceed
        # TE_THRASH_MAX_CYCLES within TE_THRASH_WINDOW_MINUTES (defaults 2/10).
        self.thrash_breaker = ThrashCircuitBreaker(
            max_cycles=int(os.getenv("TE_THRASH_MAX_CYCLES", "2")),
            window_minutes=int(os.getenv("TE_THRASH_WINDOW_MINUTES", "10")),
        )

        # Initialize Heartbeat Monitor for ecosystem fail-safe (AC: Gate behind nats_enabled)
        self.heartbeat_monitor = None
        if self.settings.nats_enabled:
            from shared.constants import NATS_URL

            # tradeengine#533: enable restricted-mode persistence when the
            # feature flag is on. The MongoDB handle is resolved lazily from the
            # already-booted distributed_lock_manager at start() time.
            persist_enabled = str(
                getattr(self.settings, "te_heartbeat_persist_enabled", "off")
            ).strip().lower() not in ("", "off", "false", "0")
            self.heartbeat_monitor = HeartbeatMonitor(
                nats_url=NATS_URL,
                subject=self.settings.nats_topic_heartbeat,
                persist_enabled=persist_enabled,
            )

    async def initialize(self) -> None:
        """Initialize dispatcher components with distributed state management"""
        try:
            # Initialize distributed lock manager first
            await distributed_lock_manager.initialize()

            # Initialize components
            await self.order_manager.initialize()
            await self.position_manager.initialize()

            # Start heartbeat monitor (AC: Check if initialized)
            if self.heartbeat_monitor:
                await self.heartbeat_monitor.start()

            # CRITICAL FIX: Initialize strategy position manager in background
            # MySQL connection attempts can take 3+ minutes and will block startup
            # Move to background task so NATS consumer can start immediately
            import asyncio

            async def init_strategy_position_manager_async() -> None:
                """Initialize strategy position manager in background"""
                try:
                    self.logger.info(
                        "Starting strategy position manager initialization in background..."
                    )
                    await strategy_position_manager.initialize()
                    self.logger.info(
                        "✅ Strategy position manager initialized successfully"
                    )
                except Exception as mysql_error:
                    self.logger.warning(
                        f"⚠️ Strategy position manager initialization failed (MySQL unavailable): {mysql_error}"
                    )
                    self.logger.warning(
                        "Positions will still work via MongoDB fallback"
                    )

            # Start initialization in background (don't await)
            asyncio.create_task(init_strategy_position_manager_async())
            self.logger.info(
                "Strategy position manager initialization started in background"
            )

            # PROACTIVE LEVERAGE SETUP (#465: offload sync REST to a thread so it
            # yields the event loop between symbols and does not starve the
            # downstream DataManager boot probe)
            if self.exchange:
                try:
                    self.logger.info("🔧 SETTING PROACTIVE LEVERAGE (10x)...")
                    from shared.constants import SUPPORTED_SYMBOLS

                    for symbol in SUPPORTED_SYMBOLS:
                        try:
                            await asyncio.to_thread(
                                self.exchange.client.futures_change_leverage,
                                symbol=symbol,
                                leverage=10,
                            )
                            self.logger.info(f"✅ Leverage set to 10x for {symbol}")
                        except Exception as lev_err:
                            self.logger.warning(
                                f"⚠️ Failed to set leverage for {symbol}: {lev_err}"
                            )
                except Exception as e:
                    self.logger.error(f"❌ PROACTIVE LEVERAGE SETUP FAILED: {e}")

            # STARTUP OCO RECONCILIATION: Rebuild active_oco_pairs from live Binance state
            # This prevents ghost-order errors (-2013/-1102) after pod restarts
            if self.exchange:
                try:
                    await self.oco_manager.reconcile_from_exchange()
                except Exception as reconcile_err:
                    self.logger.warning(
                        f"⚠️ OCO reconciliation failed (non-fatal): {reconcile_err}"
                    )

            if self.exchange and self.exchange.client:
                # #531: wire the entry-fill callback so ORDER_TRADE_UPDATE
                # FILLED events on the user-data stream publish a `filled`
                # execution event. This is the general fix that captures entry
                # fills (the OCO path only covers SL/TP exits).
                self.user_data_consumer = UserDataStreamConsumer(
                    self.exchange, on_fill=self._on_user_data_fill
                )
                await self.user_data_consumer.start()
                # AC2/AC4 (#459 — 446-C): inject the live ExchangeTruthStore into
                # PositionManager and StrategyPositionManager so risk reads can
                # source from the exchange when TE_EXCHANGE_TRUTH_STORE_ENABLED=on.
                self.position_manager.exchange_truth_store = (
                    self.user_data_consumer.store
                )
                strategy_position_manager.exchange_truth_store = (
                    self.user_data_consumer.store
                )

                # #480 — start the strategy-layer ghost reconciler now that
                # the truth store is available.  AC1 runs an immediate pass
                # inside start() so any positions left over from a previous
                # boot get reconciled before the dispatcher accepts work.
                self.strategy_position_reconciler = StrategyPositionReconciler(
                    strategy_pos_manager=strategy_position_manager,
                    store=self.user_data_consumer.store,
                )
                try:
                    await self.strategy_position_reconciler.start()
                except Exception as recon_err:
                    # Reconciler is a safety net — never block startup if
                    # the first pass hiccups (e.g. truth store still warming
                    # up).  The periodic loop will pick up on the next tick.
                    self.logger.warning(
                        "StrategyPositionReconciler start failed (non-fatal): %s",
                        recon_err,
                    )

            self.logger.info(
                "Dispatcher initialized successfully with distributed state management"
            )
        except Exception as e:
            self.logger.error(f"Dispatcher initialization error: {e}")
            raise

    async def close(self) -> None:
        """Close dispatcher components"""
        try:
            if self.heartbeat_monitor is not None:
                await self.heartbeat_monitor.stop()
            if self.strategy_position_reconciler is not None:
                await self.strategy_position_reconciler.stop()
            if self.user_data_consumer is not None:
                await self.user_data_consumer.stop()
            await self.order_manager.close()
            await self.position_manager.close()
            await distributed_lock_manager.close()
            self.logger.info("Dispatcher closed successfully")
        except Exception as e:
            self.logger.error(f"Dispatcher close error: {e}")

    async def health_check(self) -> dict[str, Any]:
        """Check dispatcher health with distributed state info"""
        try:
            # Check if components have health_check methods
            order_manager_health = {"status": "unknown"}
            position_manager_health = {"status": "unknown"}
            distributed_lock_health = {"status": "unknown"}

            if hasattr(self.order_manager, "health_check"):
                order_manager_health = await self.order_manager.health_check()
            else:
                order_manager_health = {"status": "healthy", "type": "order_manager"}

            if hasattr(self.position_manager, "health_check"):
                position_manager_health = await self.position_manager.health_check()
            else:
                position_manager_health = {
                    "status": "healthy",
                    "type": "position_manager",
                }

            if hasattr(distributed_lock_manager, "health_check"):
                distributed_lock_health = await distributed_lock_manager.health_check()
            else:
                distributed_lock_health = {
                    "status": "healthy",
                    "type": "distributed_lock_manager",
                }

            exchange_truth_store_health: dict[str, Any] = {"status": "not_started"}
            if self.user_data_consumer is not None:
                exchange_truth_store_health = (
                    await self.user_data_consumer.health_check()
                )

            return {
                "status": "healthy",
                "components": {
                    "order_manager": order_manager_health,
                    "position_manager": position_manager_health,
                    "signal_aggregator": "active",
                    "distributed_lock_manager": distributed_lock_health,
                    "exchange_truth_store": exchange_truth_store_health,
                },
            }
        except Exception as e:
            self.logger.error(f"Health check error: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def process_signal(
        self,
        signal: Signal,
        conflict_resolution: str = "strongest_wins",
        timeframe_resolution: str = "higher_timeframe_wins",
        risk_management: bool = True,
    ) -> dict[str, Any]:
        """Process a trading signal with distributed state management"""
        try:
            # Log signal
            if audit_logger.enabled:
                audit_logger.log_signal(signal.model_dump())

            # Add signal to aggregator
            self.signal_aggregator.add_signal(signal)

            # Process based on strategy mode
            if signal.strategy_mode.value == "deterministic":
                from tradeengine.signal_aggregator import DeterministicProcessor

                processor = DeterministicProcessor()
                result = await processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            elif signal.strategy_mode.value == "ml_light":
                from tradeengine.signal_aggregator import MLProcessor

                ml_processor = MLProcessor()
                result = await ml_processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            elif signal.strategy_mode.value == "llm_reasoning":
                from tradeengine.signal_aggregator import LLMProcessor

                llm_processor = LLMProcessor()
                result = await llm_processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            else:
                result = {
                    "status": "rejected",
                    "reason": f"Unknown strategy mode: {signal.strategy_mode.value}",
                }

            # Log result
            if audit_logger.enabled:
                audit_logger.log_signal(
                    {
                        "signal": signal.model_dump(),
                        "result": result,
                        "conflict_resolution": conflict_resolution,
                        "timeframe_resolution": timeframe_resolution,
                    }
                )

            return result

        except Exception as e:
            self.logger.error(f"Signal processing error: {e}")
            if audit_logger.enabled:
                audit_logger.log_error(
                    {
                        "error": str(e),
                        "signal": signal.model_dump(),
                        "endpoint": "process_signal",
                    }
                )
            return {"status": "error", "error": str(e)}

    def _generate_signal_id(self, signal: Signal) -> str:
        """Generate a unique ID for a signal for deduplication.

        Uses strategy_id, symbol, action, and timestamp (rounded to second) to identify duplicates.
        This allows detecting signals that are sent via both HTTP and NATS within the same second.
        """
        # Round timestamp to second precision to catch duplicates within 1 second
        timestamp_second = signal.timestamp.isoformat()[:19] if signal.timestamp else ""
        return (
            f"{signal.strategy_id}_{signal.symbol}_{signal.action}_{timestamp_second}"
        )

    def _cleanup_signal_cache(self) -> None:
        """Remove expired entries from the signal cache."""
        current_time = time.time()

        # Only cleanup if interval has passed
        if current_time - self.last_cache_cleanup < self.signal_cache_cleanup_interval:
            return

        # Remove entries older than TTL
        expired_ids = [
            signal_id
            for signal_id, timestamp in self.signal_cache.items()
            if current_time - timestamp > self.signal_cache_ttl
        ]

        for signal_id in expired_ids:
            del self.signal_cache[signal_id]

        if expired_ids:
            self.logger.debug(
                f"Cleaned up {len(expired_ids)} expired signal cache entries"
            )

        self.last_cache_cleanup = current_time

    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """Dispatch a signal for processing with distributed state management"""
        with tracer.start_as_current_span("dispatcher.dispatch") as span:
            # Add business context attributes to span
            span.set_attribute("signal.symbol", signal.symbol)
            span.set_attribute("signal.timeframe", signal.timeframe)
            span.set_attribute("signal.action", signal.action)
            span.set_attribute(
                "signal.type",
                signal.signal_type.value if signal.signal_type else signal.action,
            )
            span.set_attribute(
                "signal.strength",
                (
                    signal.strength.value
                    if hasattr(signal.strength, "value")
                    else str(signal.strength)
                ),
            )
            span.set_attribute("signal.confidence", signal.confidence)
            span.set_attribute("strategy.id", signal.strategy_id)
            span.set_attribute("strategy.name", signal.strategy)
            span.set_attribute("signal.current_price", signal.current_price)
            if signal.target_price:
                span.set_attribute("signal.target_price", signal.target_price)

            try:
                # Track signal reception time for latency measurement
                signal_received_at = time.time()

                # FAIL-SAFE: Check if Heartbeat Monitor is in restricted mode (AC: Follow GEMINI.md mandate)
                if (
                    self.heartbeat_monitor is not None
                    and self.heartbeat_monitor.is_restricted()
                ):
                    if signal.action == "close":
                        self.logger.warning(
                            f"⚠️  RESTRICTED_MODE: Allowing CLOSE action for {signal.symbol} despite lost CIO heartbeat"
                        )
                    else:
                        self.logger.critical(
                            f"🛑 FAIL-SAFE ABORT: Rejecting {signal.action.upper()} for {signal.symbol} "
                            f"due to RESTRICTED_MODE (CIO heartbeat lost - LLM reasoning unavailable)"
                        )
                        signals_processed.labels(
                            status="aborted_restricted", action=signal.action
                        ).inc()
                        span.set_attribute("signal.aborted", True)
                        span.set_attribute("abort.reason", "restricted_mode")
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                "RESTRICTED_MODE: CIO Heartbeat Lost",
                            )
                        )
                        await self._emit_execution_event_from_signal(
                            signal,
                            event_type="rejected",
                            reason="restricted_mode_cio_heartbeat_lost",
                        )
                        return {
                            "status": "aborted",
                            "reason": "RESTRICTED_MODE: CIO heartbeat lost, opening new positions is strictly forbidden.",
                            "symbol": signal.symbol,
                            "action": signal.action,
                        }

                # Track signal reception in metrics
                signals_received.labels(
                    strategy=signal.strategy_id,
                    symbol=signal.symbol,
                    action=signal.action,
                ).inc()

                # CIO AUDIT ENFORCEMENT (Ticket #304 / P0 #1)
                # If enforce_cio_audit is True, we only accept BUY/SELL signals from 'petrosa-cio'
                # This ensures every open signal has passed through the LLM reasoning loop.
                if self.settings.enforce_cio_audit and signal.action in ("buy", "sell"):
                    if signal.source != "petrosa-cio":
                        self.logger.critical(
                            f"🛑 CIO ENFORCEMENT FAILURE: Rejecting {signal.action.upper()} for {signal.symbol} "
                            f"from UNAUTHORIZED source '{signal.source}'. Expected 'petrosa-cio'."
                        )
                        signals_processed.labels(
                            status="rejected_unauthorized_source", action=signal.action
                        ).inc()
                        span.set_attribute("signal.rejected", True)
                        span.set_attribute("rejection.reason", "unauthorized_source")
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                f"Unauthorized source: {signal.source}",
                            )
                        )
                        await self._emit_execution_event_from_signal(
                            signal,
                            event_type="rejected",
                            reason="cio_enforcement_unauthorized_source",
                            extra={"source": signal.source},
                            rejection_source="validation",
                        )
                        return {
                            "status": "rejected",
                            "reason": f"CIO Enforcement: All {signal.action.upper()} signals must be audited by petrosa-cio. Source '{signal.source}' is not authorized.",
                            "symbol": signal.symbol,
                            "action": signal.action,
                            "source": signal.source,
                        }
                    else:
                        self.logger.info(
                            f"🔒 CIO AUDIT VERIFIED: Signal from {signal.source} for {signal.symbol} {signal.action.upper()}"
                        )

                # Enhanced logging for signal reception
                self.logger.info(
                    f"📩 SIGNAL RECEIVED: {signal.strategy_id} | "
                    f"{signal.symbol} {signal.action.upper()} @ {signal.current_price} | "
                    f"Confidence: {signal.confidence:.2%} | "
                    f"Timeframe: {signal.timeframe}"
                )

                # Check for duplicate signals (prevents double processing from HTTP + NATS)
                signal_id = self._generate_signal_id(signal)
                current_time = time.time()

                if signal_id in self.signal_cache:
                    age = current_time - self.signal_cache[signal_id]
                    if age < self.signal_cache_ttl:
                        # Duplicate detected within TTL window
                        self.logger.warning(
                            f"🚫 DUPLICATE SIGNAL DETECTED AND REJECTED: {signal.strategy_id} | "
                            f"{signal.symbol} {signal.action.upper()} | "
                            f"Age: {age:.2f}s | Original received {age:.2f}s ago"
                        )
                        signals_duplicate.labels(
                            strategy=signal.strategy_id,
                            symbol=signal.symbol,
                            action=signal.action,
                        ).inc()
                        span.set_attribute("signal.duplicate", True)
                        span.set_status(trace.Status(trace.StatusCode.OK))
                        return {
                            "status": "duplicate",
                            "reason": f"Duplicate signal detected (age: {age:.2f}s)",
                            "original_time": self.signal_cache[signal_id],
                            "duplicate_age_seconds": age,
                        }

                # Store signal in cache
                self.signal_cache[signal_id] = current_time

                # Cleanup old cache entries periodically
                self._cleanup_signal_cache()

                # Handle hold signals
                if signal.action == "hold":
                    self.logger.info(
                        f"⏸️  HOLD SIGNAL FILTERED: {signal.strategy_id} | "
                        f"{signal.symbol} | No action taken"
                    )
                    signals_processed.labels(status="hold", action="hold").inc()
                    span.set_attribute("signal.action", "hold")
                    span.set_status(trace.Status(trace.StatusCode.OK))
                    return {"status": "hold", "reason": "Signal indicates hold action"}

                # NEW: Check accumulation cooldown
                position_side = "LONG" if signal.action == "buy" else "SHORT"
                position_key = (signal.symbol, position_side)

                if position_key in self.position_manager.positions:
                    existing_quantity = self.position_manager.positions[position_key][
                        "quantity"
                    ]

                    if existing_quantity > 0:  # Position exists
                        # Check cooldown
                        if position_key in self.last_accumulation_time:
                            from shared.constants import ACCUMULATION_COOLDOWN_SECONDS

                            elapsed = (
                                time.time() - self.last_accumulation_time[position_key]
                            )

                            if elapsed < ACCUMULATION_COOLDOWN_SECONDS:
                                remaining = ACCUMULATION_COOLDOWN_SECONDS - elapsed
                                self.logger.info(
                                    f"⏱️ ACCUMULATION COOLDOWN: {signal.symbol} {position_side} "
                                    f"- {remaining:.0f}s remaining (existing qty: {existing_quantity})"
                                )
                                span.set_attribute("signal.rejected", True)
                                span.set_attribute(
                                    "rejection.reason", "accumulation_cooldown"
                                )
                                span.set_status(trace.Status(trace.StatusCode.OK))
                                await self._emit_execution_event_from_signal(
                                    signal,
                                    event_type="rejected",
                                    reason="accumulation_cooldown",
                                    extra={"remaining_sec": int(remaining)},
                                    rejection_source="stale_signal",
                                )
                                return {
                                    "status": "rejected",
                                    "reason": f"Accumulation cooldown active ({remaining:.0f}s/{ACCUMULATION_COOLDOWN_SECONDS}s)",
                                }

                # Log signal processing
                self.logger.info(
                    f"⚙️  PROCESSING SIGNAL: {signal.strategy_id} | "
                    f"{signal.symbol} {signal.action.upper()}"
                )

                # Process the signal
                result = await self.process_signal(signal)

                # If processing was successful, execute the order with distributed lock
                # Signal processors can return "success" or "executed" - both are valid for order execution
                signal_status = result.get("status")
                if signal_status in ("success", "executed"):
                    self.logger.info(
                        f"✅ SIGNAL VALIDATED: {signal.strategy_id} | "
                        f"Converting to order for {signal.symbol} | "
                        f"Processing status: {signal_status}"
                    )

                    # FIX: Pass the processed order_params to _signal_to_order
                    order_params = result.get("order_params")
                    order = self._signal_to_order(signal, order_params)

                    # Store signal for strategy position creation later
                    self.order_to_signal[order.order_id] = signal

                    # Store signal received time for latency tracking
                    order.meta = order.meta or {}
                    order.meta["signal_received_at"] = signal_received_at

                    # Generate unique signal fingerprint for deduplication across replicas
                    # This prevents multiple pods from processing the same signal
                    signal_fingerprint = self._generate_signal_fingerprint(signal)

                    self.logger.info(
                        f"🔐 ACQUIRING DISTRIBUTED LOCK: signal_{signal_fingerprint}"
                    )
                    # Execute order with distributed lock to ensure consensus
                    # Lock key includes signal fingerprint to prevent duplicate processing
                    try:
                        execution_result = (
                            await distributed_lock_manager.execute_with_lock(
                                f"signal_{signal_fingerprint}",
                                self._execute_order_with_consensus,
                                order,
                            )
                        )
                    except Exception as lock_error:
                        if "Failed to acquire lock" in str(lock_error):
                            self.logger.info(
                                f"🔒 LOCK ACQUISITION FAILED: {signal.strategy_id} | "
                                f"Signal already being processed by another pod - SKIPPING"
                            )
                            signals_processed.labels(
                                status="skipped_duplicate", action=signal.action
                            ).inc()
                            span.set_attribute("signal.skipped", True)
                            span.set_attribute("skip.reason", "lock_acquisition_failed")
                            span.set_status(trace.Status(trace.StatusCode.OK))
                            return {
                                "status": "skipped_duplicate",
                                "reason": "Signal already being processed by another pod",
                                "signal_fingerprint": signal_fingerprint,
                            }
                        else:
                            # Re-raise other lock-related errors
                            raise

                    result["execution_result"] = execution_result
                    _exec_status = execution_result.get("status", "unknown")
                    if _exec_status in (
                        "error",
                        "rejected",
                        "cancelled",
                        "failed",
                        "rolled_back",
                        "rollback_failed",
                    ):
                        result["status"] = "exchange_failed"
                    else:
                        result["status"] = "executed"

                    # NEW: Update last accumulation time if order was executed successfully
                    if execution_result.get("status") in (
                        "filled",
                        "partially_filled",
                        "NEW",
                    ):
                        if position_key in self.position_manager.positions:
                            if (
                                self.position_manager.positions[position_key][
                                    "quantity"
                                ]
                                > 0
                            ):
                                self.last_accumulation_time[position_key] = time.time()

                    self.logger.info(
                        f"🎯 SIGNAL DISPATCH COMPLETE: {signal.strategy_id} | "
                        f"Execution status: {execution_result.get('status')} | "
                        f"Dispatch status: {result['status']}"
                    )
                    signals_processed.labels(
                        status=result["status"], action=signal.action
                    ).inc()
                elif signal_status == "rejected":
                    self.logger.info(
                        f"⛔ SIGNAL REJECTED: {signal.strategy_id} | "
                        f"Reason: {result.get('reason', 'Unknown')}"
                    )
                    signals_processed.labels(
                        status="rejected", action=signal.action
                    ).inc()
                    await self._emit_execution_event_from_signal(
                        signal,
                        event_type="rejected",
                        reason=str(result.get("reason", "risk_or_signal_rejected"))[
                            :128
                        ],
                        # Per #651: the upstream `_execute_order_with_consensus`
                        # path (the only producer of result["status"]=="rejected"
                        # at this point) already populated the order's
                        # rejection fields. Use the dict's hint when present.
                        rejection_source=result.get("rejection_source"),
                    )
                else:
                    self.logger.warning(
                        f"⚠️  SIGNAL VALIDATION FAILED: {signal.strategy_id} | "
                        f"Status: {signal_status} | Reason: {result.get('reason', 'Unknown')}"
                    )
                    signals_processed.labels(
                        status="failed", action=signal.action
                    ).inc()

                span.set_attribute("dispatch.status", result.get("status", "unknown"))
                span.set_status(trace.Status(trace.StatusCode.OK))
                return result

            except Exception as e:
                self.logger.error(
                    f"❌ DISPATCH ERROR: {signal.strategy_id if hasattr(signal, 'strategy_id') else 'Unknown'} | "
                    f"Error: {e}",
                    exc_info=True,
                )
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                span.record_exception(e)
                return {"status": "error", "error": str(e)}

    async def _execute_order_with_consensus(self, order: TradeOrder) -> dict[str, Any]:
        """Execute order with distributed consensus"""
        try:
            # Check risk limits with distributed state
            risk_checks_total.labels(
                check_type="position_limits",
                result="checking",
                exchange=order.exchange,
            ).inc()

            if not await self.position_manager.check_position_limits(order):
                # Per #651: map the position_manager's structured
                # rejection_reason into the P6.2 Literal[rejection_source]
                # vocabulary so the audit-trail carries dashboard-queryable
                # data. position_manager.rejection_reason is set by
                # check_position_limits via attribute mutation.
                pm_reason = (
                    getattr(self.position_manager, "rejection_reason", None)
                    or "position_limits_exceeded"
                )
                pm_source_map: dict[
                    str,
                    Literal[
                        "risk_check",
                        "exchange",
                        "stale_signal",
                        "whitelist",
                        "balance",
                        "validation",
                    ],
                ] = {
                    "symbol_not_allowed": "whitelist",
                    "insufficient_margin": "balance",
                    "refresh_failure": "risk_check",
                    "absolute_position_size": "risk_check",
                    "position_size_pct": "risk_check",
                    "portfolio_exposure": "risk_check",
                    "algo_order_limits": "risk_check",
                }
                rej_source = pm_source_map.get(pm_reason, "risk_check")
                order.mark_rejected(source=rej_source, reason=pm_reason)
                risk_rejections_total.labels(
                    reason="position_limits_exceeded",
                    symbol=order.symbol,
                    exchange=order.exchange,
                ).inc()
                risk_checks_total.labels(
                    check_type="position_limits",
                    result="rejected",
                    exchange=order.exchange,
                ).inc()
                self.logger.warning(
                    f"⛔ RISK REJECTION: Position limits exceeded for {order.symbol} "
                    f"(source={rej_source}, reason={pm_reason})"
                )
                await self._emit_execution_event_from_order(
                    order,
                    {"status": "rejected"},
                    event_type="rejected",
                    reason=pm_reason,
                )
                return {
                    "status": "rejected",
                    "reason": pm_reason,
                    "rejection_source": rej_source,
                }

            risk_checks_total.labels(
                check_type="position_limits",
                result="passed",
                exchange=order.exchange,
            ).inc()

            risk_checks_total.labels(
                check_type="daily_loss_limits",
                result="checking",
                exchange=order.exchange,
            ).inc()

            if not await self.position_manager.check_daily_loss_limits():
                # Per #651: daily-loss is a risk_check rejection; mark the
                # order with the structured fields so the audit-trail row
                # carries the same data shape as position-limit rejects.
                order.mark_rejected(
                    source="risk_check",
                    reason="daily_loss_limits_exceeded",
                )
                risk_rejections_total.labels(
                    reason="daily_loss_limits_exceeded",
                    symbol=order.symbol,
                    exchange=order.exchange,
                ).inc()
                risk_checks_total.labels(
                    check_type="daily_loss_limits",
                    result="rejected",
                    exchange=order.exchange,
                ).inc()
                self.logger.warning(
                    f"⛔ RISK REJECTION: Daily loss limits exceeded for {order.symbol}"
                )
                await self._emit_execution_event_from_order(
                    order,
                    {"status": "rejected"},
                    event_type="rejected",
                    reason="daily_loss_limits_exceeded",
                )
                return {
                    "status": "rejected",
                    "reason": "daily_loss_limits_exceeded",
                    "rejection_source": "risk_check",
                }

            risk_checks_total.labels(
                check_type="daily_loss_limits",
                result="passed",
                exchange=order.exchange,
            ).inc()

            # -- AC2 + AC3: Leverage Bound check (FR64, P6.4) ----------------
            risk_checks_total.labels(
                check_type="leverage_bound",
                result="checking",
                exchange=order.exchange,
            ).inc()
            try:
                from tradeengine.config_manager import TradingConfigManager

                # Resolve config for this order's scope
                _strategy_id: str | None = order.strategy_metadata.get("strategy_id")
                _config_mgr: TradingConfigManager | None = getattr(
                    self, "config_manager", None
                )
                if _config_mgr is not None:
                    _resolved = await _config_mgr.get_config(
                        symbol=order.symbol,
                        side=("LONG" if order.side == "buy" else "SHORT"),
                        strategy_id=_strategy_id,
                    )
                else:
                    from tradeengine.defaults import get_default_parameters

                    _resolved = get_default_parameters()

                # Collect open position leverages for AC3 by looking up each
                # position's resolved config. Falls back to 10x if config lookup
                # fails or config_manager is unavailable.
                _open_leverages: list[int] = []
                for _pos in strategy_position_manager.get_all_open_strategy_positions():
                    try:
                        _pos_strat = _pos.get("strategy_id")
                        _pos_sym = _pos.get("symbol", order.symbol)
                        _pos_side = _pos.get("side", "LONG")
                        if _config_mgr is not None and _pos_strat:
                            _pos_cfg = await _config_mgr.get_config(
                                symbol=_pos_sym,
                                side=_pos_side,
                                strategy_id=_pos_strat,
                            )
                            _open_leverages.append(int(_pos_cfg.get("leverage", 10)))
                        else:
                            _open_leverages.append(10)
                    except Exception:
                        _open_leverages.append(10)

                _lb_pass, _lb_reason = self.leverage_bound_guard.check(
                    order, _resolved, _open_leverages
                )
            except Exception as _lb_exc:
                self.logger.warning(
                    f"Leverage bound guard raised an unexpected error for "
                    f"{order.symbol} — failing open (order proceeds): {_lb_exc}"
                )
                _lb_pass, _lb_reason = True, ""

            if not _lb_pass:
                order.mark_rejected(source="leverage_bound", reason=_lb_reason)
                risk_checks_total.labels(
                    check_type="leverage_bound",
                    result="rejected",
                    exchange=order.exchange,
                ).inc()
                self.logger.warning(
                    f"RISK REJECTION: Leverage bound exceeded for {order.symbol} — {_lb_reason}"
                )
                await self._emit_execution_event_from_order(
                    order,
                    {"status": "rejected"},
                    event_type="rejected",
                    reason=_lb_reason[:128],
                )
                return {
                    "status": "rejected",
                    "reason": _lb_reason,
                    "rejection_source": "leverage_bound",
                }

            risk_checks_total.labels(
                check_type="leverage_bound",
                result="passed",
                exchange=order.exchange,
            ).inc()
            # -- End AC2+AC3 --------------------------------------------------

            # Execute order
            result = await self.execute_order(order)

            # Update position with distributed state management and create position record
            # Market orders return "NEW" status immediately, which is valid for risk management
            self.logger.info(
                f"🔍 ORDER RESULT CHECK: status={result.get('status') if result else None}, has_result={result is not None}"
            )

            if result and result.get("status") in ["filled", "partially_filled", "NEW"]:
                self.logger.info(
                    f"✅ ORDER STATUS VALID FOR POSITION UPDATE: {order.symbol}"
                )
                # CRITICAL FIX: Position update with retry logic
                position_updated = False
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        self.logger.info(
                            f"🔄 Updating position for {order.symbol} (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.wait_for(
                            self.position_manager.update_position(order, result),
                            timeout=10.0,  # Increased from 5s to 10s
                        )
                        self.logger.info(
                            f"✅ Position updated | event=position_updated | symbol={order.symbol} | "
                            f"order_id={order.order_id} | position_id={result.get('position_id')}"
                        )
                        position_updated = True
                        self.logger.info(
                            f"🔍 DEBUG: position_updated={position_updated}, breaking from retry loop"
                        )
                        break  # Success, exit retry loop
                    except TimeoutError:
                        if attempt < max_retries - 1:
                            backoff = 2**attempt
                            self.logger.warning(
                                f"⏱️ Position update timed out for {order.symbol} "
                                f"(attempt {attempt + 1}/{max_retries}), retrying in {backoff}s..."
                            )
                            await asyncio.sleep(backoff)
                        else:
                            self.logger.error(
                                f"⏱️ Position update failed after {max_retries} attempts for {order.symbol} - ABORTING risk management"
                            )
                            return result  # Exit without placing risk management orders
                    except Exception as e:
                        if attempt < max_retries - 1:
                            backoff = 2**attempt
                            self.logger.warning(
                                f"❌ Position update failed for {order.symbol}: {e} "
                                f"(attempt {attempt + 1}/{max_retries}), retrying in {backoff}s..."
                            )
                            await asyncio.sleep(backoff)
                        else:
                            self.logger.error(
                                f"❌ Position update failed after {max_retries} attempts for {order.symbol}: {e} - ABORTING risk management"
                            )
                            return result  # Exit without placing risk management orders

                # Only create position record if position update succeeded
                self.logger.info(
                    f"🔍 DEBUG: Exited retry loop for {order.symbol}, position_updated={position_updated}"
                )
                self.logger.info(
                    f"🔍 DEBUG: About to check position_updated={position_updated} for {order.symbol}"
                )
                if position_updated:
                    self.logger.info(
                        f"🔍 DEBUG: ENTERED if position_updated block for {order.symbol}"
                    )
                    try:
                        await asyncio.wait_for(
                            self.position_manager.create_position_record(order, result),
                            timeout=5.0,
                        )
                        self.logger.info(
                            f"✅ Position record created for {order.symbol}"
                        )
                    except TimeoutError:
                        self.logger.error(
                            f"⏱️ Position record creation timed out for {order.symbol} - continuing anyway"
                        )
                    except Exception as e:
                        self.logger.error(
                            f"❌ Position record creation failed for {order.symbol}: {e} - continuing anyway"
                        )

                    # Create strategy position for advanced analytics
                    try:
                        signal = self.order_to_signal.get(order.order_id)
                        if signal:
                            strategy_position_id = await asyncio.wait_for(
                                strategy_position_manager.create_strategy_position(
                                    signal, order, result
                                ),
                                timeout=5.0,
                            )
                            self.logger.info(
                                f"✅ Strategy position {strategy_position_id} created for {signal.strategy_id}"
                            )

                            # NEW: Map order to strategy position for OCO attribution
                            self.order_to_strategy_position[order.order_id] = (
                                strategy_position_id
                            )
                            self.logger.info(
                                f"📍 Mapped order {order.order_id} → strategy_position {strategy_position_id}"
                            )

                            # Clean up signal mapping
                            del self.order_to_signal[order.order_id]
                        else:
                            self.logger.warning(
                                f"⚠️  No signal found for order {order.order_id} - skipping strategy position creation"
                            )
                    except TimeoutError:
                        self.logger.error(
                            f"⏱️ Strategy position creation timed out for {order.symbol} - continuing anyway"
                        )
                    except Exception as e:
                        self.logger.error(
                            f"❌ Strategy position creation failed for {order.symbol}: {e} - continuing anyway"
                        )

                    # Only place risk management orders if position was successfully updated
                    self.logger.info(
                        f"🔍 DEBUG: About to place risk management orders for {order.symbol}"
                    )
                    self.logger.info(
                        f"🛡️ ATTEMPTING TO PLACE RISK MANAGEMENT ORDERS for {order.symbol} | position_updated={position_updated}"
                    )

                    # #371: Route CONDITIONAL/NEW entries to the deferred-OCO path.
                    # Returning a non-None result means "handled — skip the immediate
                    # OCO placement and the rollback-guarded block below."
                    deferred_result = await self._route_conditional_pending_to_defer(
                        order, result
                    )
                    if deferred_result is not None:
                        return deferred_result

                    try:
                        self.logger.info(
                            f"🔧 Calling _place_risk_management_orders() for {order.symbol}"
                        )
                        await asyncio.wait_for(
                            self._place_risk_management_orders(order, result),
                            timeout=10.0,  # Longer timeout for exchange API calls
                        )
                        self.logger.info(
                            f"✅ Risk management orders placed for {order.symbol}"
                        )
                    except Exception as e:
                        self.logger.error(
                            f"[CRITICAL] OCO placement failed. Initiating atomic rollback for symbol {order.symbol}: {e}"
                        )
                        # Atomic Rollback (AC2 / RC#2 of #424): close the position immediately
                        # via a MARKET reduceOnly order. When local state is incomplete
                        # (no position_id, filled_qty<=0) we fall back to Binance positionRisk
                        # so the position cannot be left unhedged on the exchange.
                        rollback_reason = "atomic_rollback_oco_failure"
                        rollback_position_id = getattr(order, "position_id", None)
                        filled_qty: float = 0.0
                        try:
                            # Prefer the executed amount from result; fall back to the order
                            # amount when result.amount is missing or zero.
                            result_qty = result.get("amount", 0.0)
                            if isinstance(result_qty, str):
                                try:
                                    result_qty = float(result_qty)
                                except (ValueError, TypeError):
                                    result_qty = 0.0
                            filled_qty = (
                                float(result_qty)
                                if result_qty and result_qty > 0
                                else float(order.amount or 0.0)
                            )

                            if filled_qty <= 0:
                                # RC#2: re-derive from Binance positionRisk before skipping —
                                # the exchange is ground truth, the local result dict is not.
                                derived_qty = await self._fetch_binance_position_qty(
                                    order.symbol, order.position_side
                                )
                                if derived_qty > 0:
                                    self.logger.warning(
                                        f"⚠️ filled_qty non-positive ({filled_qty}); derived "
                                        f"rollback qty {derived_qty} from Binance positionRisk for {order.symbol}"
                                    )
                                    filled_qty = derived_qty
                                else:
                                    self.logger.warning(
                                        f"⚠️ Skipping atomic rollback for {order.symbol}: "
                                        f"filled_qty={filled_qty} and Binance reports zero position"
                                    )
                                    result["status"] = "rolled_back_skipped"
                                    result["error"] = f"Risk management failure: {e}"
                                    result["rollback_skipped_reason"] = (
                                        f"non_positive_filled_qty_and_binance_zero: local={filled_qty}"
                                    )
                                    return result

                            # RC#2: close_position_with_cleanup reaches the exchange via
                            # (symbol, position_side, quantity); position_id is only used
                            # for OCO-cancel + local position-record cleanup, both of which
                            # short-circuit cleanly when it is absent.
                            rollback_result = await self.close_position_with_cleanup(
                                position_id=rollback_position_id or "",
                                symbol=order.symbol,
                                position_side=order.position_side,
                                quantity=filled_qty,
                                reason=rollback_reason,
                            )
                            # close_position_with_cleanup catches internal exch errors
                            # and returns status="failed"/"error" instead of raising —
                            # so we MUST inspect the return value, otherwise a real
                            # Binance failure would be reported as a successful rollback
                            # while the position stays open (defeats AC2 entirely).
                            if not rollback_result or not rollback_result.get(
                                "position_closed"
                            ):
                                rollback_status = (
                                    rollback_result.get("status", "unknown")
                                    if rollback_result
                                    else "no_result"
                                )
                                close_err = (
                                    rollback_result.get("error", "")
                                    if rollback_result
                                    else ""
                                )
                                raise RuntimeError(
                                    f"close_position_with_cleanup did not close position "
                                    f"(status={rollback_status}, error={close_err!r})"
                                )
                            self.logger.info(
                                f"✅ Atomic rollback successful for {order.symbol}"
                            )

                            # Log critical event to audit trail
                            if audit_logger.enabled:
                                audit_logger.log_trade(
                                    {
                                        "event": "atomic_rollback",
                                        "symbol": order.symbol,
                                        "position_id": rollback_position_id,
                                        "reason": "risk_management_failure",
                                        "error": str(e),
                                        "rollback_result": rollback_result,
                                    }
                                )

                            # rolled_back: full path with position_id (OCO + record cleared).
                            # rolled_back_partial: position closed on Binance but no local
                            # position record existed — surfaces the missing-link so ops
                            # can reconcile without leaving the position open.
                            result["status"] = (
                                "rolled_back"
                                if rollback_position_id
                                else "rolled_back_partial"
                            )

                        except Exception as rollback_error:
                            self.logger.error(
                                f"❌ CRITICAL: Atomic rollback FAILED for {order.symbol}: {rollback_error}"
                            )
                            result["status"] = "rollback_failed"
                            result["rollback_error"] = str(rollback_error)

                            # RC#2: surface to ops via metric + NATS alert so the
                            # unhedged-on-Binance state is observable. Both wrapped in
                            # try/except — the alert path must never break the caller.
                            reason_label = type(rollback_error).__name__
                            try:
                                atomic_rollback_failed_total.labels(
                                    symbol=order.symbol,
                                    reason=reason_label,
                                ).inc()
                            except Exception:
                                self.logger.debug(
                                    "atomic_rollback_failed metric inc failed",
                                    exc_info=True,
                                )
                            try:
                                await alert_publisher.publish(
                                    alert_name=f"rollback_failed.{order.symbol}",
                                    severity="critical",
                                    payload={
                                        "symbol": order.symbol,
                                        "position_side": order.position_side,
                                        "filled_qty": filled_qty,
                                        "rollback_reason": rollback_reason,
                                        "rollback_error": str(rollback_error),
                                        "oco_failure_error": str(e),
                                    },
                                )
                            except Exception:
                                self.logger.debug(
                                    "rollback_failed alert publish failed",
                                    exc_info=True,
                                )

                        # Return result with appropriate error status
                        result["error"] = f"Risk management failure: {e}"
                        return result

            return result

        except Exception as e:
            self.logger.error(f"Order execution with consensus error: {e}")
            return {"status": "error", "error": str(e)}

    async def _emit_execution_event_from_signal(
        self,
        signal: Signal,
        *,
        event_type: ExecutionEventType,
        reason: str,
        order_id: str = "",
        extra: dict[str, Any] | None = None,
        rejection_source: (
            Literal[
                "risk_check",
                "exchange",
                "stale_signal",
                "whitelist",
                "balance",
                "validation",
            ]
            | None
        ) = None,
        rejected_at: datetime | None = None,
    ) -> None:
        """Emit an execution.events.<strategy_id> message keyed off a Signal.

        Per #651 (P6.2 follow-up): when `rejection_source` is provided, the
        structured P6.2 rejection fields ride along in `extra` so the
        data-manager audit-trail captures them on the rejection row. The
        ExecutionEvent model on the subscriber side keeps unknown fields in
        its `payload` dict, so no contract bump is required.
        """
        merged_extra: dict[str, Any] = {
            "symbol": getattr(signal, "symbol", None),
            "side": getattr(signal, "action", None),
        }
        if extra:
            merged_extra.update(extra)
        if rejection_source is not None:
            ts = rejected_at or datetime.now(UTC)
            merged_extra["rejection_source"] = rejection_source
            merged_extra["rejection_reason"] = reason
            merged_extra["rejected_at"] = ts.isoformat()
        try:
            await execution_event_publisher.publish(
                event_type=event_type,
                strategy_id=signal.strategy_id,
                order_id=order_id,
                reason=reason,
                decision_id=signal.decision_id,
                extra=merged_extra,
            )
        except Exception as emit_err:
            self.logger.warning(
                "execution_event emit (signal-keyed) failed for %s: %s",
                signal.strategy_id,
                emit_err,
            )

        await self._feed_halt_detector(
            event_type=event_type,
            rejection_source=rejection_source,
            decision_id=signal.decision_id,
        )

    async def _emit_execution_event_from_order(
        self,
        order: TradeOrder,
        result: dict[str, Any],
        *,
        event_type: ExecutionEventType,
        reason: str,
    ) -> None:
        """Emit an execution.events.<strategy_id> message keyed off an executed TradeOrder.

        Per #651 (P6.2 follow-up): if the order has been through
        `TradeOrder.mark_rejected(...)` (i.e. `rejection_source` is set),
        the structured P6.2 rejection fields ride along in `extra` so the
        data-manager audit-trail captures them on the rejection row.
        """
        strategy_meta = order.strategy_metadata or {}
        strategy_id = strategy_meta.get("strategy_id") or "unknown"
        decision_id = strategy_meta.get("decision_id")
        # Prefer exchange order_id (Binance numeric id) when present; fall back to internal id.
        exchange_order_id = result.get("order_id") if isinstance(result, dict) else None
        order_id = str(exchange_order_id or order.order_id or "")

        fill_qty = None
        fill_price = None
        fee = None
        fee_asset = None
        fill_time: str | None = None
        pnl = None
        if isinstance(result, dict):
            # Binance fills carry total filled qty; partial_fill needs this to be meaningful.
            fill_qty = result.get("amount") or result.get("filled") or None
            fill_price = result.get("fill_price") or result.get("average_price")
            fee = result.get("fees")
            fee_asset = result.get("fee_asset")
            if fee_asset is None:
                fills = result.get("fills") or []
                if isinstance(fills, list):
                    for fill in fills:
                        if isinstance(fill, dict) and fill.get("commissionAsset"):
                            fee_asset = fill.get("commissionAsset")
                            break
            raw_fill_time = result.get("fill_time") or result.get("timestamp")
            if raw_fill_time is not None:
                if isinstance(raw_fill_time, datetime):
                    ts = (
                        raw_fill_time
                        if raw_fill_time.tzinfo
                        else raw_fill_time.replace(tzinfo=UTC)
                    )
                    fill_time = ts.astimezone(UTC).isoformat()
                elif isinstance(raw_fill_time, int | float):
                    # Binance transactTime is ms epoch; simulator may emit ISO already.
                    epoch = float(raw_fill_time)
                    if epoch > 1e12:
                        epoch /= 1000.0
                    fill_time = datetime.fromtimestamp(epoch, tz=UTC).isoformat()
                else:
                    fill_time = str(raw_fill_time)
            pnl = result.get("pnl")

        extra: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.amount,
        }
        if fill_qty is not None:
            extra["fill_qty"] = fill_qty
            extra["fill_quantity"] = fill_qty
        if fill_price is not None:
            extra["fill_price"] = fill_price
            # PnL calculator in data-manager reads `price`; keep both keys in sync.
            extra["price"] = fill_price
        if fill_time is not None:
            extra["fill_time"] = fill_time
        if fee is not None:
            extra["fee"] = fee
        if fee_asset is not None:
            extra["fee_asset"] = fee_asset
        if pnl is not None:
            extra["pnl"] = pnl
        elif event_type in ("filled", "partial_fill"):
            # Explicit null so audit consumers can distinguish "unknown" from omitted.
            extra["pnl"] = None
        if order.rejection_source is not None:
            extra["rejection_source"] = order.rejection_source
            extra["rejection_reason"] = order.rejection_reason
            if order.rejected_at is not None:
                extra["rejected_at"] = order.rejected_at.isoformat()
        try:
            await execution_event_publisher.publish(
                event_type=event_type,
                strategy_id=strategy_id,
                order_id=order_id,
                reason=reason,
                decision_id=decision_id,
                extra=extra,
            )
        except Exception as emit_err:
            self.logger.warning(
                "execution_event emit (order-keyed) failed for %s: %s",
                order.order_id,
                emit_err,
            )

        await self._feed_halt_detector(
            event_type=event_type,
            rejection_source=order.rejection_source,
            decision_id=decision_id,
        )

    async def _feed_halt_detector(
        self,
        *,
        event_type: ExecutionEventType,
        rejection_source: str | None,
        decision_id: str | None,
    ) -> None:
        """Hand one execution event off to the halt-suspected detector.

        Best-effort: never raises into the caller — the order / rejection
        path tolerates an unhealthy observability subsystem.
        """
        try:
            if event_type == "rejected":
                await halt_suspected_detector.on_rejection(
                    rejection_source=rejection_source,
                    decision_id=decision_id,
                )
            elif event_type in ("placed", "filled", "partial_fill"):
                await halt_suspected_detector.on_completion()
        except Exception as exc:
            self.logger.warning(
                "halt_suspected_detector hook failed for event_type=%s: %s",
                event_type,
                exc,
            )

    def _signal_to_order(
        self, signal: Signal, order_params: dict[str, Any] | None = None
    ) -> TradeOrder:
        """Convert a signal to a trade order with dynamic minimum amounts"""
        import uuid

        # Use parameters from signal, but allow overrides from processed order_params
        current_signal = signal.model_copy()
        if order_params:
            if "position_size_pct" in order_params:
                current_signal.position_size_pct = float(
                    order_params["position_size_pct"]
                )
            if "stop_loss_pct" in order_params:
                current_signal.stop_loss_pct = float(order_params["stop_loss_pct"])
            if "take_profit_pct" in order_params:
                current_signal.take_profit_pct = float(order_params["take_profit_pct"])
            if "side" in order_params:
                current_signal.action = order_params["side"]

        # Calculate order amount based on signal quantity or dynamic minimum
        amount = self._calculate_order_amount(current_signal)

        # Generate unique position ID for tracking
        position_id = str(uuid.uuid4())

        # Determine position side for hedge mode (buy=LONG, sell=SHORT)
        position_side = "LONG" if current_signal.action == "buy" else "SHORT"

        # Collect all signal parameters for position tracking
        strategy_metadata = {
            "signal_id": current_signal.signal_id or current_signal.id,
            "strategy_id": current_signal.strategy_id,
            "strategy_mode": current_signal.strategy_mode.value,
            "source": current_signal.source,
            "strategy": current_signal.strategy,
            "timeframe": current_signal.timeframe,
            "confidence": current_signal.confidence,
            "strength": current_signal.strength.value,
            "indicators": current_signal.indicators,
            "rationale": current_signal.rationale,
            "llm_reasoning": current_signal.llm_reasoning,
            "metadata": current_signal.metadata,
            "meta": current_signal.meta,
            "decision_id": current_signal.decision_id,
        }

        # CRITICAL DEBUG: Log TP/SL values from signal
        self.logger.info(
            f"🔍 SIGNAL TO ORDER CONVERSION | Symbol: {current_signal.symbol} | "
            f"Signal SL: {current_signal.stop_loss} | Signal TP: {current_signal.take_profit} | "
            f"Signal SL_pct: {current_signal.stop_loss_pct} | Signal TP_pct: {current_signal.take_profit_pct}"
        )

        # Create the order
        order = TradeOrder(
            order_id=f"order_{current_signal.strategy_id}_{datetime.now(UTC).timestamp()}",
            symbol=current_signal.symbol,
            side=current_signal.action,
            type=current_signal.order_type.value,
            amount=amount,
            target_price=current_signal.current_price,
            stop_loss=current_signal.stop_loss,
            take_profit=current_signal.take_profit,
            stop_loss_pct=current_signal.stop_loss_pct,
            take_profit_pct=current_signal.take_profit_pct,
            conditional_price=current_signal.conditional_price,
            conditional_direction=current_signal.conditional_direction,
            conditional_timeout=current_signal.conditional_timeout,
            iceberg_quantity=current_signal.iceberg_quantity,
            client_order_id=current_signal.client_order_id,
            status=OrderStatus.PENDING,
            reduce_only=False,  # Orders from signals are position-opening
            filled_amount=0.0,
            average_price=0.0,
            time_in_force=current_signal.time_in_force.value,
            position_size_pct=current_signal.position_size_pct,
            created_at=current_signal.timestamp,
            updated_at=current_signal.timestamp,
            simulate=(
                current_signal.meta.get("simulate", False)
                if current_signal.meta
                else False
            ),
            # Hedge mode position tracking
            position_id=position_id,
            position_side=position_side,
            exchange="binance",
            strategy_metadata=strategy_metadata,
        )

        return order

    def _calculate_order_amount(self, signal: Signal) -> float:
        """Calculate order amount ensuring MIN_NOTIONAL is met and supporting position_size_pct"""
        try:
            # Import here to avoid circular imports
            from tradeengine.api import binance_exchange

            # Calculate minimum amount needed to meet MIN_NOTIONAL
            current_price = signal.current_price or 0
            min_amount = binance_exchange.calculate_min_order_amount(
                signal.symbol, current_price
            )

            # Determine whether to use percentage or fixed quantity
            # 1. Use quantity IF it's provided AND pct is missing (None)
            # This allows legacy fixed-quantity signals and the test suite to work.
            if (
                signal.quantity
                and signal.quantity > 0
                and signal.position_size_pct is None
            ):
                if signal.quantity < min_amount:
                    self.logger.warning(
                        f"Signal quantity {signal.quantity} is below minimum {min_amount} "
                        f"for {signal.symbol} at ${current_price:.2f}. Using minimum."
                    )
                    amount = min_amount
                else:
                    amount = signal.quantity
            # 2. Otherwise use position_size_pct IF it's provided
            elif signal.position_size_pct and signal.position_size_pct > 0:
                total_portfolio_value = self.position_manager.total_portfolio_value
                if total_portfolio_value > 0 and current_price > 0:
                    # amount = (total_value * pct) / price
                    calculated_amount = (
                        total_portfolio_value * signal.position_size_pct
                    ) / current_price

                    if calculated_amount < min_amount:
                        self.logger.warning(
                            f"Calculated amount {calculated_amount} from {signal.position_size_pct:.2%} size "
                            f"is below minimum {min_amount}. Using minimum."
                        )
                        amount = min_amount
                    else:
                        amount = calculated_amount
                        self.logger.info(
                            f"Amount calculated from position_size_pct ({signal.position_size_pct:.2%}): {amount}"
                        )
                else:
                    self.logger.warning(
                        f"Cannot calculate amount from pct: portfolio_value={total_portfolio_value}, price={current_price}. Using minimum."
                    )
                    amount = min_amount
            # 3. Fallback to quantity if it exists but pct logic failed/was skipped
            elif signal.quantity and signal.quantity > 0:
                amount = max(signal.quantity, min_amount)
            else:
                # Use calculated minimum
                amount = min_amount

            self.logger.info(
                f"Order amount final for {signal.symbol}: amount={amount}, "
                f"signal_qty={signal.quantity}, signal_pct={signal.position_size_pct}, "
                f"min_required={min_amount}, current_price={current_price}"
            )

            return amount

        except Exception as e:
            self.logger.error(
                f"Error calculating order amount for {signal.symbol}: {e}",
                exc_info=True,
            )
            # Fallback: Calculate amount to meet minimum notional with safety margin
            # Use a $110 target notional, which is above the $100 MIN_NOTIONAL for BTCUSDT
            current_price = signal.current_price or 0

            if current_price > 0:
                # Target $110 notional value to avoid MIN_NOTIONAL rejections
                fallback_amount = 110.0 / current_price
                self.logger.warning(
                    f"Using fallback amount {fallback_amount} for {signal.symbol} "
                    f"(target notional: $110.00 at ${current_price:.2f})"
                )
                return fallback_amount
            else:
                # No reliable price available; cannot safely compute an amount
                # that satisfies MIN_NOTIONAL. Abort rather than sending an
                # almost certainly invalid order size.
                self.logger.error(
                    f"Cannot calculate valid order amount for {signal.symbol}: "
                    f"no current price available in fallback path"
                )
                raise ValueError(
                    f"Cannot calculate order amount for {signal.symbol} without a valid price"
                )

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a trading order with detailed logging"""
        import time

        start_time = time.time()

        with tracer.start_as_current_span("dispatcher.execute_order") as span:
            # Add business context attributes to span
            span.set_attribute("order.id", order.order_id or "unknown")
            span.set_attribute("order.symbol", order.symbol)
            span.set_attribute("order.side", order.side)
            span.set_attribute("order.type", order.type)
            span.set_attribute("order.quantity", order.amount)
            if order.target_price:
                span.set_attribute("order.price", order.target_price)
            if order.position_side:
                span.set_attribute("order.position_side", order.position_side)
            span.set_attribute("order.simulate", order.simulate)

            try:
                # Enhanced logging for order execution
                self.logger.info(
                    f"🔨 EXECUTING ORDER: {order.symbol} {order.side.upper()} "
                    f"{order.amount} @ {order.target_price} | "
                    f"Type: {order.type} | ID: {order.order_id}"
                )

                # Log order
                if audit_logger.enabled:
                    audit_logger.log_order(order.model_dump())

                # Execute order on Binance exchange
                if order.simulate:
                    # Simulated order - just track locally
                    self.logger.info(
                        f"🎭 SIMULATION MODE: Order {order.order_id} simulated"
                    )
                    result = {"status": "pending", "simulated": True}
                    await self.order_manager.track_order(order, result)
                else:
                    # Real order - execute on Binance
                    if self.exchange:
                        try:
                            self.logger.info(
                                f"📤 SENDING TO BINANCE: {order.symbol} {order.side} "
                                f"{order.amount} @ {order.target_price}"
                            )
                            result = await self.exchange.execute(order)
                            await self.order_manager.track_order(order, result)

                            # Log success with details
                            self.logger.info(
                                f"✅ BINANCE ORDER EXECUTED: {order.symbol} {order.side} | "
                                f"Status: {result.get('status')} | "
                                f"Order ID: {result.get('order_id', 'N/A')} | "
                                f"Fill Price: {result.get('fill_price', 'N/A')} | "
                                f"Result: {result}"
                            )
                        except Exception as exchange_error:
                            self.logger.error(
                                f"❌ BINANCE EXCHANGE ERROR: {order.symbol} {order.side} | "
                                f"Error: {exchange_error} | Order ID: {order.order_id}",
                                exc_info=True,
                            )
                            # Per #651: an exchange-thrown exception is a
                            # rejection_source="exchange" outcome — mark the
                            # order so the audit-trail row carries the P6.2
                            # structured fields.
                            order.mark_rejected(
                                source="exchange",
                                reason=str(exchange_error)[:200],
                            )
                            result = {"status": "error", "error": str(exchange_error)}
                            await self.order_manager.track_order(order, result)
                    else:
                        # No exchange provided, just track locally
                        self.logger.warning(
                            f"⚠️  NO EXCHANGE CONFIGURED: Order {order.order_id} tracked locally only"
                        )
                        result = {"status": "pending", "no_exchange": True}
                        await self.order_manager.track_order(order, result)

                # Per #651: if the exchange's wrapper returned a "failed"
                # status (binance.py:_format_error_result) without raising,
                # treat that as an exchange-side rejection too — only mark
                # if not already marked by the except clause above.
                if (
                    result is not None
                    and result.get("status") == "failed"
                    and order.rejection_source is None
                ):
                    order.mark_rejected(
                        source="exchange",
                        reason=str(result.get("error", "exchange_failed"))[:200],
                    )

                # Log result
                if audit_logger.enabled:
                    audit_logger.log_order(
                        {
                            "order": order.model_dump(),
                            "result": result,
                        }
                    )

                self.logger.info(
                    f"📊 ORDER EXECUTION COMPLETE: {order.order_id} | Status: {result.get('status')}"
                )

                # Track order execution metrics
                execution_time = time.time() - start_time
                order_execution_time.labels(
                    symbol=order.symbol, side=order.side
                ).observe(execution_time)
                orders_executed.labels(
                    symbol=order.symbol,
                    side=order.side,
                    status=result.get("status", "unknown"),
                ).inc()

                # Track business metrics
                order_status = result.get("status", "unknown")

                # Emit order execution by type metric
                orders_executed_by_type.labels(
                    order_type=order.type,
                    side=order.side,
                    symbol=order.symbol,
                    exchange=order.exchange,
                ).inc()

                # Calculate and emit order latency (signal → execution)
                if order.meta and "signal_received_at" in order.meta:
                    signal_latency = time.time() - order.meta["signal_received_at"]
                    order_execution_latency_seconds.labels(
                        symbol=order.symbol,
                        order_type=order.type,
                        exchange=order.exchange,
                    ).observe(signal_latency)

                    self.logger.info(
                        f"📊 ORDER LATENCY: {signal_latency:.3f}s from signal receipt to execution complete"
                    )

                # Track order failures
                if order_status in ["error", "rejected", "cancelled"]:
                    failure_reason = result.get("error", "unknown")
                    order_failures_total.labels(
                        symbol=order.symbol,
                        order_type=order.type,
                        failure_reason=str(failure_reason)[
                            :50
                        ],  # Truncate long error messages
                        exchange=order.exchange,
                    ).inc()

                # Update span with execution result
                span.set_attribute("order.status", result.get("status", "unknown"))
                if result.get("order_id"):
                    span.set_attribute("order.exchange_id", result.get("order_id"))
                if result.get("fill_price"):
                    span.set_attribute("order.fill_price", result.get("fill_price"))
                span.set_status(trace.Status(trace.StatusCode.OK))

                # Emit execution.events.<strategy_id> for order lifecycle (#586 AC2/AC4).
                # Mapping Binance/exchange statuses onto our four-event vocabulary.
                exch_status_raw = str(result.get("status", "")).lower()
                mapped_event: ExecutionEventType | None = None
                emit_reason = ""
                if exch_status_raw in ("filled",):
                    mapped_event = "filled"
                    emit_reason = "binance_filled"
                elif exch_status_raw in ("partially_filled", "partial", "partial_fill"):
                    mapped_event = "partial_fill"
                    fq = result.get("amount") or 0
                    emit_reason = f"partial_{fq}_of_{order.amount}"
                elif exch_status_raw in (
                    "new",
                    "pending",
                    "accepted",
                    "open",
                    "working",
                ):
                    mapped_event = "placed"
                    emit_reason = (
                        "binance_accepted" if not order.simulate else "simulated_placed"
                    )
                elif exch_status_raw in (
                    "rejected",
                    "expired",
                    "cancelled",
                    "canceled",
                    "failed",
                    "error",
                ):
                    mapped_event = "rejected"
                    err = result.get("error")
                    emit_reason = f"exchange_{exch_status_raw}" + (
                        f": {str(err)[:80]}" if err else ""
                    )

                if mapped_event is not None:
                    await self._emit_execution_event_from_order(
                        order,
                        result,
                        event_type=mapped_event,
                        reason=emit_reason,
                    )
                return result

            except Exception as e:
                self.logger.error(
                    f"❌ ORDER EXECUTION FAILED: {order.order_id} | Error: {e}",
                    exc_info=True,
                )
                if audit_logger.enabled:
                    audit_logger.log_error(
                        {
                            "error": str(e),
                            "order": order.model_dump(),
                            "endpoint": "execute_order",
                        }
                    )
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                span.record_exception(e)
                await self._emit_execution_event_from_order(
                    order,
                    {"status": "error", "error": str(e)},
                    event_type="rejected",
                    reason=f"order_execution_exception: {str(e)[:80]}",
                )
                return {"status": "error", "error": str(e)}

    async def _on_user_data_fill(self, order_obj: dict[str, Any]) -> None:
        """Publish a `filled` execution event for an entry fill (#531).

        Invoked by ExchangeTruthStore when an ORDER_TRADE_UPDATE reports a
        FILLED status. The raw Binance `o` payload carries neither strategy_id
        nor decision_id, so they are recovered from the strategy position that
        owns this entry order id. SL/TP exit fills are handled separately by
        the OCO close path (``_emit_oco_exit_filled_event``) and are skipped
        here when reduce_only is set, to avoid a duplicate `filled` event.

        Best-effort: never raises into the truth-store callback.
        """
        try:
            symbol = order_obj.get("s", "")
            order_id = str(order_obj.get("i", ""))
            side = order_obj.get("S", "")
            is_reduce_only = bool(order_obj.get("R", False))
            order_type = order_obj.get("o", "")

            # SL/TP exits are reduce-only and/or STOP/TAKE_PROFIT order types;
            # the OCO close path already emits their `filled` event. Skip here
            # so a single exit does not produce two `filled` events.
            if is_reduce_only or order_type in (
                "STOP_MARKET",
                "TAKE_PROFIT_MARKET",
                "STOP",
                "TAKE_PROFIT",
            ):
                # #534 (H6 of #977): a FILLED SL/TP leg is the exact signal the
                # 2s poll waits for. When the WS-wake flag is on, nudge the
                # OCO monitor so it re-polls immediately instead of waiting up
                # to 2s. The poll still owns the cancel/close decision, so this
                # only shortens latency and cannot double-cancel. Best-effort.
                if settings.te_oco_ws_wake_enabled:
                    try:
                        self.oco_manager.notify_oco_leg_fill(symbol, order_id)
                    except Exception as wake_err:
                        self.logger.debug(
                            "#534: OCO wake nudge failed for %s: %s",
                            order_id,
                            wake_err,
                        )
                self.logger.debug(
                    "#531: skipping user-data fill for reduce-only/SL-TP order "
                    "%s (%s) — OCO path owns its filled event",
                    order_id,
                    order_type,
                )
                return

            # Recover strategy_id + decision_id from the owning strategy position.
            from tradeengine.strategy_position_manager import (
                strategy_position_manager,
            )

            strategy_id = "unknown"
            decision_id: str | None = None
            position = None
            if strategy_position_manager is not None:
                position = (
                    strategy_position_manager.get_strategy_position_by_entry_order_id(
                        order_id
                    )
                )
            if position:
                strategy_id = position.get("strategy_id", "unknown")
                decision_id = position.get("decision_id")

            if not decision_id:
                self.logger.warning(
                    "#531: entry fill for order %s (%s) has no decision_id — the "
                    "`filled` event will be dropped by the data-manager consumer.",
                    order_id,
                    strategy_id,
                )

            # Binance ORDER_TRADE_UPDATE fill fields:
            #   L = last filled price, z = cumulative filled qty,
            #   n = commission, N = commission asset, rp = realized pnl,
            #   T = transaction time (ms epoch).
            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            fill_price = _to_float(order_obj.get("L")) or _to_float(order_obj.get("ap"))
            fill_qty = _to_float(order_obj.get("z")) or _to_float(order_obj.get("q"))
            fee = _to_float(order_obj.get("n"))
            fee_asset = order_obj.get("N")
            pnl = _to_float(order_obj.get("rp"))
            fill_time = None
            raw_ts = order_obj.get("T")
            if isinstance(raw_ts, int | float):
                epoch = float(raw_ts)
                if epoch > 1e12:
                    epoch /= 1000.0
                fill_time = datetime.fromtimestamp(epoch, tz=UTC).isoformat()

            extra: dict[str, Any] = {
                "symbol": symbol,
                "side": side,
            }
            if fill_price is not None:
                extra["fill_price"] = fill_price
                extra["price"] = fill_price
            if fill_qty is not None:
                extra["fill_quantity"] = fill_qty
                extra["fill_qty"] = fill_qty
            if fee is not None:
                extra["fee"] = fee
            if fee_asset is not None:
                extra["fee_asset"] = fee_asset
            if fill_time is not None:
                extra["fill_time"] = fill_time
            extra["pnl"] = pnl  # entry fills usually have rp=0; keep explicit

            await execution_event_publisher.publish(
                event_type="filled",
                strategy_id=strategy_id,
                order_id=order_id,
                reason="user_data_stream_fill",
                decision_id=decision_id,
                extra=extra,
            )
        except Exception as emit_err:
            self.logger.warning(
                "#531: failed to publish user-data `filled` event: %s",
                emit_err,
            )

    def get_cio_state(self, symbol: str) -> dict[str, Any]:
        """
        Aggregates real-time state data for the CIO TriggerContext.
        Ground-truth only, no fabrication.
        """
        from shared.config import settings

        portfolio_data = self.position_manager.get_cio_portfolio_summary(symbol)
        active_orders = self.order_manager.get_active_orders()

        # Calculate symbol-specific order count
        symbol_orders_count = sum(1 for o in active_orders if o.get("symbol") == symbol)

        # Build ground-truth state object
        return {
            "portfolio": portfolio_data,
            "risk_limits": {
                "max_drawdown_pct": settings.max_daily_loss_pct,  # Closest mapping in shared/config
                "max_orders_global": getattr(settings, "max_algo_orders", 10),
                "max_orders_per_symbol": getattr(
                    settings, "max_algo_orders_per_symbol", 2
                ),
                "max_position_size_usd": getattr(
                    settings, "max_position_size_usd", 1000.0
                ),
            },
            "env_stats": {
                "global_drawdown_pct": max(-self.position_manager.get_daily_pnl(), 0.0)
                / max(self.position_manager.total_portfolio_value, 1.0),
                "open_orders_global": len(active_orders),
                "open_orders_symbol": symbol_orders_count,
                "available_capital_usd": self.position_manager.total_portfolio_value,
            },
        }

    def get_signal_summary(self) -> dict[str, Any]:
        """Get signal processing summary"""
        return self.signal_aggregator.get_signal_summary()

    def set_strategy_weight(self, strategy_id: str, weight: float) -> None:
        """Set weight for a strategy"""
        self.signal_aggregator.set_strategy_weight(strategy_id, weight)

    def get_positions(self) -> dict[str, Any]:
        """Get all positions"""
        return self.position_manager.get_positions()

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get specific position"""
        return self.position_manager.get_position(symbol)

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary"""
        return self.position_manager.get_portfolio_summary()

    def get_active_orders(self) -> list[dict[str, Any]]:
        """Get all active orders"""
        return self.order_manager.get_active_orders()

    def get_conditional_orders(self) -> list[dict[str, Any]]:
        """Get all conditional orders"""
        return self.order_manager.get_conditional_orders()

    def get_order_history(self) -> list[dict[str, Any]]:
        """Get order history"""
        return self.order_manager.get_order_history()

    def get_order_summary(self) -> dict[str, Any]:
        """Get order summary"""
        return self.order_manager.get_order_summary()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """Get specific order"""
        return self.order_manager.get_order(order_id)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        return self.order_manager.cancel_order(order_id)

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information"""
        try:
            # This would integrate with the exchange to get real account info
            # For now, return a placeholder
            return {
                "account_type": "simulated",
                "balances": {
                    "BTC": {"free": "0.1", "locked": "0.0"},
                    "USDT": {"free": "5000.0", "locked": "0.0"},
                },
                "total_balance_usdt": 5000.0,
                "positions": self.get_positions(),
                "pnl": {
                    "total": 0.0,
                    "daily": self.position_manager.get_daily_pnl(),
                    "unrealized": self.position_manager.get_total_unrealized_pnl(),
                },
                "risk_metrics": {
                    "max_position_size": 0.1,
                    "max_daily_loss": 100.0,
                    "current_exposure": self.position_manager._calculate_portfolio_exposure(),
                },
            }
        except Exception as e:
            self.logger.error(f"Error getting account info: {e}")
            return {"error": str(e)}

    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol"""
        try:
            # This would integrate with price feeds
            # For now, return a placeholder
            base_prices = {
                "BTCUSDT": 45000.0,
                "ETHUSDT": 3000.0,
                "ADAUSDT": 0.5,
            }
            return base_prices.get(symbol, 100.0)
        except Exception as e:
            self.logger.error(f"Error getting price for {symbol}: {e}")
            return 0.0

    def get_metrics(self) -> dict[str, Any]:
        """Get system metrics"""
        return {
            "positions_count": len(self.get_positions()),
            "active_orders_count": len(self.get_active_orders()),
            "conditional_orders_count": len(self.get_conditional_orders()),
            "daily_pnl": self.position_manager.get_daily_pnl(),
            "total_unrealized_pnl": self.position_manager.get_total_unrealized_pnl(),
        }

    async def _route_conditional_pending_to_defer(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """#371: Route CONDITIONAL+NEW entries to the deferred-OCO path.

        Returns:
            - None  → caller should proceed with immediate OCO placement (default path)
            - dict  → the (possibly mutated) result; caller must return it immediately
                      and skip the rollback-guarded block. A CONDITIONAL+NEW order has
                      no live exchange position yet, so defer-path failures must NOT
                      trigger atomic MARKET rollback against a non-existent position.
        """
        if not self.oco_manager._is_conditional_pending_entry(order, result):
            return None

        entry_oid = str(result.get("order_id") or "")
        if not entry_oid:
            # Exchange accepted a CONDITIONAL but returned no order_id.
            # Surface loudly — silent return would leave the eventual fill
            # unprotected by SL/TP.
            self.logger.error(
                f"[CRITICAL] CONDITIONAL entry for {order.symbol} returned "
                f"status=NEW but no order_id — cannot defer OCO. "
                f"Position will be UNPROTECTED if the entry triggers. "
                f"result={result}"
            )
            result["status"] = "deferred_oco_failed"
            result["error"] = (
                "Missing entry_order_id for CONDITIONAL — OCO cannot be deferred"
            )
            return result

        try:
            await self.oco_manager.defer_oco_until_filled(order, entry_oid)
            self.logger.info(
                f"⏸️ Skipping immediate risk-management placement for {order.symbol}: "
                f"entry is CONDITIONAL/NEW, OCO deferred until FILLED"
            )
            return result
        except Exception as defer_err:
            # Defer-path failure: log loudly but DO NOT trigger rollback.
            # The CONDITIONAL is on the exchange in NEW state; closing it
            # requires cancel_order, not market-close.
            self.logger.error(
                f"[CRITICAL] Failed to defer OCO for CONDITIONAL "
                f"{order.symbol} (entry {entry_oid}): {defer_err}. "
                f"Eventual fill will be UNPROTECTED unless operator intervenes."
            )
            result["status"] = "deferred_oco_failed"
            result["error"] = f"defer_oco_until_filled raised: {defer_err}"
            return result

    async def _reanchor_entry_price_if_stale(
        self, symbol: str, entry_price: float
    ) -> float:
        """Re-anchor a stale entry/reference price to live market (#501 AC2).

        Returns the live market price when ``entry_price`` deviates beyond the
        symbol's PERCENT_PRICE band; otherwise returns ``entry_price`` unchanged.
        Any resolution failure is non-fatal — the original ``entry_price`` is
        preserved so this guard can never make the fill path worse.
        """
        if entry_price <= 0 or self.exchange is None:
            return entry_price
        try:
            live_price = float(await self.exchange._get_current_price(symbol))
        except Exception as ref_err:  # pragma: no cover - defensive
            self.logger.warning(
                f"⚠️ #501 re-anchor: could not resolve live price for "
                f"{symbol}: {ref_err}. Keeping entry_price {entry_price}."
            )
            return entry_price
        if live_price <= 0:
            return entry_price

        # Resolve the PERCENT_PRICE band; default to ±10% if unavailable.
        mult_up, mult_down = 1.10, 0.90
        try:
            band = self.exchange.get_percent_price_filter(symbol)
            mult_up = float(band.get("multiplierUp", mult_up))
            mult_down = float(band.get("multiplierDown", mult_down))
        except Exception:  # pragma: no cover - defensive
            pass

        upper = live_price * mult_up
        lower = live_price * mult_down
        if entry_price > upper or entry_price < lower:
            deviation_pct = abs(entry_price - live_price) / live_price * 100.0
            self.logger.warning(
                f"⚠️ #501 STALE ANCHOR: entry_price {entry_price} for {symbol} "
                f"deviates {deviation_pct:.2f}% from live market {live_price} "
                f"(band {lower:.6f}–{upper:.6f}). Re-anchoring SL/TP reference "
                f"to live market price."
            )
            return live_price
        return entry_price

    async def _place_risk_management_orders(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place stop loss and take profit orders with OCO behavior after successful position execution"""
        try:
            # ENTRY LOG - Always log when this method is called
            self.logger.info(
                f"🔧 ENTERING _place_risk_management_orders | Symbol: {order.symbol} | "
                f"SL: {order.stop_loss} | TP: {order.take_profit} | "
                f"Exchange: {self.exchange is not None} | Reduce_only: {order.reduce_only}"
            )

            if not self.exchange:
                self.logger.warning(
                    "No exchange configured, cannot place risk management orders"
                )
                return

            # Only place risk management orders for position-opening orders (not reduce_only)
            if order.reduce_only:
                self.logger.debug(
                    "Skipping risk management orders for reduce_only order"
                )
                return

            # AC1 (#460): check-before-place — skip if exchange already has a protective order
            if self.user_data_consumer is not None:
                _PROTECTIVE_TYPES = frozenset(
                    {"STOP_MARKET", "STOP", "TAKE_PROFIT_MARKET", "TAKE_PROFIT"}
                )
                existing_protective = [
                    o
                    for o in self.user_data_consumer.store.get_open_orders(order.symbol)
                    if o.order_type.upper() in _PROTECTIVE_TYPES
                ]
                if existing_protective:
                    order_placement_skipped_total.labels(reason="already_armed").inc()
                    self.logger.info(
                        f"⏭️ Skipping protective order placement for {order.symbol}: "
                        f"exchange already has {len(existing_protective)} armed protective "
                        f"order(s) ({[o.order_type for o in existing_protective]}). "
                        "Skipping to prevent duplicates."
                    )
                    return

            # Ensure entry_price is a float for OCO math and logging
            # Fallback to order.target_price if fill_price is missing or zero
            entry_price_raw = result.get("fill_price")

            def is_zero(val: Any) -> bool:
                if not val:
                    return True
                try:
                    return float(val) == 0
                except (ValueError, TypeError):
                    return True

            if is_zero(entry_price_raw):
                entry_price_raw = result.get("price")
            if is_zero(entry_price_raw):
                entry_price_raw = order.target_price

            try:
                entry_price = (
                    float(entry_price_raw) if entry_price_raw is not None else 0.0
                )
            except (ValueError, TypeError):
                self.logger.warning(
                    f"⚠️ Could not cast entry_price '{entry_price_raw}' to float"
                )
                entry_price = 0.0

            # #501 AC2: re-anchor a STALE reference price to live market.
            # The old code only fell back to live market when entry_price <= 0.
            # A non-zero-but-stale signal price (order.target_price =
            # signal.current_price) would still be preferred, so all downstream
            # SL/TP percentages were computed off a suspect anchor. Force the
            # live-market fallback whenever the resolved entry_price deviates
            # beyond the symbol's PERCENT_PRICE band from the current market.
            if entry_price > 0 and self.exchange is not None:
                entry_price = await self._reanchor_entry_price_if_stale(
                    order.symbol, entry_price
                )

            # MIN SL DISTANCE FLOOR: enforce a minimum safe distance against
            # premature stop-outs from strategy anomalies or micro-volatility.
            from shared.constants import MIN_SL_DISTANCE_PCT

            if (
                order.stop_loss_pct is not None
                and order.stop_loss_pct > 0
                and order.stop_loss_pct < MIN_SL_DISTANCE_PCT
            ):
                self.logger.warning(
                    f"⚠️ SL FLOOR: stop_loss_pct {order.stop_loss_pct * 100:.3f}% is below "
                    f"floor {MIN_SL_DISTANCE_PCT * 100:.3f}% for {order.symbol}. "
                    f"Overriding to floor."
                )
                order.stop_loss_pct = MIN_SL_DISTANCE_PCT
                # Drop any precomputed absolute stop_loss so it gets recomputed
                # from the floored percentage below.
                order.stop_loss = None

            # If an absolute stop_loss was provided directly, enforce the floor
            # on its implied distance from entry as well.
            if (
                order.stop_loss
                and order.stop_loss > 0
                and entry_price > 0
                and not order.stop_loss_pct
            ):
                implied_pct = abs(entry_price - order.stop_loss) / entry_price
                if implied_pct < MIN_SL_DISTANCE_PCT:
                    self.logger.warning(
                        f"⚠️ SL FLOOR: stop_loss {order.stop_loss} implies "
                        f"{implied_pct * 100:.3f}% distance from entry {entry_price} "
                        f"for {order.symbol}, below floor {MIN_SL_DISTANCE_PCT * 100:.3f}%. "
                        f"Overriding to floor."
                    )
                    order.stop_loss_pct = MIN_SL_DISTANCE_PCT
                    order.stop_loss = None

            # CRITICAL FIX: Calculate absolute prices from percentages if missing
            if not order.stop_loss and order.stop_loss_pct and entry_price > 0:
                if order.side == "buy":  # LONG
                    order.stop_loss = entry_price * (1 - order.stop_loss_pct)
                else:  # SHORT
                    order.stop_loss = entry_price * (1 + order.stop_loss_pct)
                self.logger.info(
                    f"Calculated stop_loss price: {order.stop_loss} from {order.stop_loss_pct * 100}%"
                )

            if not order.take_profit and order.take_profit_pct and entry_price > 0:
                if order.side == "buy":  # LONG
                    order.take_profit = entry_price * (1 + order.take_profit_pct)
                else:  # SHORT
                    order.take_profit = entry_price * (1 - order.take_profit_pct)
                self.logger.info(
                    f"Calculated take_profit price: {order.take_profit} from {order.take_profit_pct * 100}%"
                )

            # SL/TP DIRECTION VALIDATION (#479): always enforce position-side-correct
            # protective price BEFORE OCO placement. Three robustness gaps the
            # 2026-06-18 BNB/LTC/LINK incident exposed:
            #   1. The old guard required entry_price > 0, but fresh MARKET orders
            #      return result.fill_price=None and target_price=None — entry_price
            #      collapses to 0 and every check below it short-circuits.
            #   2. The old guard used order.side ("buy"/"sell"); position_side is
            #      the unambiguous source of truth ("LONG"/"SHORT").
            #   3. The old guard only corrected when stop_loss_pct was provided;
            #      strategies that send absolute SL with no pct hint slipped through
            #      with a warning, shipping the wrong-side price to the exchange.
            position_side_for_check: str | None = None
            if order.position_side:
                _ps_raw = str(order.position_side).upper()
                if _ps_raw in ("LONG", "SHORT"):
                    position_side_for_check = _ps_raw
            if position_side_for_check is None:
                position_side_for_check = (
                    "SHORT" if (order.side or "").lower() == "sell" else "LONG"
                )

            reference_price = entry_price
            if reference_price <= 0 and self.exchange is not None:
                try:
                    reference_price = float(
                        await self.exchange._get_current_price(order.symbol)
                    )
                except Exception as ref_err:
                    self.logger.warning(
                        f"⚠️ Could not resolve reference price for "
                        f"{order.symbol} direction check: {ref_err}"
                    )
                    reference_price = 0.0

            if reference_price > 0:
                from tradeengine.risk.sl_tp_direction import (
                    correct_protective_price,
                )

                try:
                    sl_safety_floor_pct = (
                        float(Settings().te_min_sl_distance_pct) / 100.0
                    )
                except Exception:
                    sl_safety_floor_pct = 0.06

                if order.stop_loss and order.stop_loss > 0:
                    sl_corr = correct_protective_price(
                        kind="SL",
                        position_side=position_side_for_check,  # type: ignore[arg-type]
                        requested_price=float(order.stop_loss),
                        requested_pct=order.stop_loss_pct,
                        reference_price=reference_price,
                        min_distance_pct=sl_safety_floor_pct,
                    )
                    if sl_corr.was_corrected:
                        self.logger.warning(
                            f"⚠️ SL DIRECTION FIX ({position_side_for_check}): "
                            f"{sl_corr.reason}"
                        )
                        order.stop_loss = sl_corr.price

                if order.take_profit and order.take_profit > 0:
                    tp_corr = correct_protective_price(
                        kind="TP",
                        position_side=position_side_for_check,  # type: ignore[arg-type]
                        requested_price=float(order.take_profit),
                        requested_pct=order.take_profit_pct,
                        reference_price=reference_price,
                        min_distance_pct=0.0,
                    )
                    if tp_corr.was_corrected:
                        self.logger.warning(
                            f"⚠️ TP DIRECTION FIX ({position_side_for_check}): "
                            f"{tp_corr.reason}"
                        )
                        order.take_profit = tp_corr.price

                # MARKET-SIDE STOP VALIDATION (#551): correct_protective_price
                # only guarantees the SL is on the correct side of ENTRY. Once
                # the market crosses entry (position underwater), that same stop
                # can sit on the WRONG side of the LIVE market → Binance -2021
                # "immediately trigger" → OCO cancels the surviving leg →
                # position left NAKED (XLMUSDT SHORT 2026-08-20). Re-anchor the
                # SL to the correct side of live market, or flatten if no stop
                # can be placed without immediate trigger.
                if order.stop_loss and order.stop_loss > 0:
                    from tradeengine.risk.sl_tp_direction import (
                        enforce_market_side_stop,
                    )

                    market_price = 0.0
                    try:
                        market_price = float(
                            await self.exchange._get_current_price(order.symbol)
                        )
                    except Exception as mkt_err:
                        self.logger.warning(
                            f"⚠️ #551: could not resolve live market for "
                            f"{order.symbol} market-side SL check: {mkt_err}"
                        )

                    if market_price > 0:
                        mkt_decision = enforce_market_side_stop(
                            position_side=position_side_for_check,  # type: ignore[arg-type]
                            stop_price=float(order.stop_loss),
                            market_price=market_price,
                            min_distance_pct=sl_safety_floor_pct,
                        )
                        if mkt_decision.should_flatten:
                            self.logger.error(
                                f"🛑 #551 FLATTEN ESCALATION ({position_side_for_check}): "
                                f"{mkt_decision.reason}. Flattening reduce-only "
                                f"MARKET instead of shipping a guaranteed -2021 SL."
                            )
                            try:
                                flat_qty = float(
                                    result.get("amount") or order.amount or 0.0
                                )
                            except (TypeError, ValueError):
                                flat_qty = float(order.amount or 0.0)
                            if flat_qty > 0:
                                await self.close_position_with_cleanup(
                                    position_id=order.position_id or "",
                                    symbol=order.symbol,
                                    position_side=order.position_side or "",
                                    quantity=flat_qty,
                                    reason="sl_unplaceable_market_crossed_551",
                                )
                            else:
                                self.logger.error(
                                    f"🛑 #551: cannot flatten {order.symbol} — "
                                    f"no resolvable quantity (result.amount="
                                    f"{result.get('amount')}, order.amount="
                                    f"{order.amount}). Position may be naked."
                                )
                            # Do NOT proceed to OCO placement with an
                            # unplaceable stop.
                            return
                        if mkt_decision.was_reanchored:
                            self.logger.warning(
                                f"⚠️ SL MARKET-SIDE RE-ANCHOR "
                                f"({position_side_for_check}): {mkt_decision.reason}"
                            )
                            order.stop_loss = mkt_decision.price
            else:
                self.logger.warning(
                    f"⚠️ Skipping SL/TP direction check for {order.symbol}: "
                    f"no reference price available (entry={entry_price}, "
                    f"market fetch failed). Risk orders may be wrong-side."
                )

            # Check if both SL and TP are specified for OCO behavior
            if (
                order.stop_loss
                and order.stop_loss > 0
                and order.take_profit
                and order.take_profit > 0
            ):
                # Use OCO logic for paired SL/TP orders
                self.logger.info(f"🔄 PLACING OCO ORDERS FOR {order.symbol}")

                # Get the filled quantity with robust extraction
                # Market orders may return amount=0 when status is NEW (not yet filled)
                filled_quantity = result.get("amount", None)

                # Convert to float if it's a string
                if filled_quantity is not None and isinstance(filled_quantity, str):
                    try:
                        filled_quantity = float(filled_quantity)
                    except (ValueError, TypeError):
                        filled_quantity = None

                # Use order.amount if result amount is 0, None, or invalid
                if filled_quantity is None or filled_quantity <= 0:
                    filled_quantity = order.amount
                    self.logger.info(
                        f"Using order.amount ({order.amount}) for OCO - "
                        f"result amount was {result.get('amount')}"
                    )
                else:
                    self.logger.info(f"Using filled amount {filled_quantity} for OCO")

                # Final safety check
                if filled_quantity <= 0:
                    self.logger.error(
                        f"Cannot place OCO orders: invalid quantity {filled_quantity} "
                        f"(order.amount={order.amount}, result.amount={result.get('amount')})"
                    )
                    return

                # NEW: Get strategy context for OCO attribution
                strategy_position_id = self.order_to_strategy_position.get(
                    order.order_id
                )

                # Ensure entry_price is a float for OCO math and logging
                # Fallback to order.target_price if fill_price is missing or zero
                entry_price_raw = result.get("fill_price")

                def is_zero(val: Any) -> bool:
                    if not val:
                        return True
                    try:
                        return float(val) == 0
                    except (ValueError, TypeError):
                        return True

                if is_zero(entry_price_raw):
                    entry_price_raw = result.get("price")
                if is_zero(entry_price_raw):
                    entry_price_raw = order.target_price

                try:
                    entry_price = (
                        float(entry_price_raw) if entry_price_raw is not None else 0.0
                    )
                except (ValueError, TypeError):
                    self.logger.warning(
                        f"⚠️ Could not cast entry_price '{entry_price_raw}' to float"
                    )
                    entry_price = 0.0

                self.logger.info(
                    f"🎯 Placing OCO with strategy context: "
                    f"strategy_position_id={strategy_position_id}, entry_price={entry_price}"
                )

                oco_result = await self.oco_manager.place_oco_orders(
                    position_id=order.position_id or "",
                    symbol=order.symbol,
                    position_side=order.position_side or "",
                    quantity=filled_quantity,
                    stop_loss_price=order.stop_loss or 0.0,
                    take_profit_price=order.take_profit or 0.0,
                    strategy_position_id=strategy_position_id,  # NEW
                    entry_price=entry_price,  # NEW
                )

                if oco_result["status"] == "success":
                    self.logger.info(
                        f"✅ OCO ORDERS PLACED SUCCESSFULLY FOR {order.symbol}"
                    )

                    # Update position record with OCO order IDs
                    if order.position_id:
                        await self.position_manager.update_position_risk_orders(
                            order.position_id,
                            stop_loss_order_id=oco_result.get("sl_order_id"),
                            take_profit_order_id=oco_result.get("tp_order_id"),
                        )

                    # AC3 of #424 (2026-05-30 OCO incident): also propagate
                    # the real Binance algo-order IDs into the
                    # strategy_position record so stops-health verification
                    # has the right values to check against the exchange.
                    # set_strategy_position_orders validates the IDs via
                    # RiskOrderIds, rejecting price-shaped strings.
                    if strategy_position_id:
                        try:
                            await (
                                strategy_position_manager.set_strategy_position_orders(
                                    strategy_position_id=strategy_position_id,
                                    sl_order_id=oco_result.get("sl_order_id"),
                                    tp_order_id=oco_result.get("tp_order_id"),
                                )
                            )
                        except Exception as exc:
                            # Swallow validation/persistence errors here so
                            # OCO-placement success is not undone by a
                            # downstream record-keeping failure — but log
                            # loudly so the bad-id case stays visible.
                            self.logger.error(
                                "set_strategy_position_orders failed for %s: %s",
                                strategy_position_id,
                                exc,
                            )
                elif oco_result.get("error") == "duplicate_oco":
                    # Position already has an active OCO pair — expected when multiple
                    # strategies accumulate into the same exchange position.
                    # The existing OCO protects the full exchange position; placing
                    # additional individual SL/TP orders would consume algo order slots
                    # and push towards the Binance 10-order-per-symbol limit.
                    strategy_label = strategy_position_id or order.order_id or "unknown"
                    self.logger.info(
                        f"✅ OCO CONSOLIDATED: {order.symbol} "
                        f"({oco_result.get('exchange_position_key')}) already has "
                        f"{oco_result.get('active_pairs', 1)} active OCO pair(s). "
                        f"order={strategy_label} added to position without "
                        f"separate risk orders — existing OCO provides coverage."
                    )
                elif oco_result.get("status") == "skipped_no_position_on_exchange":
                    # AC3 of #445: OCOManager pre-check found no position on the
                    # exchange (rare in normal flow — usually a stale-state /
                    # post-restart situation). Falling back to individual SL/TP
                    # orders here would produce the same -4509 GTE loop the gate
                    # is preventing. Skip the fallback; the protective-order
                    # request was for a phantom position.
                    self.logger.warning(
                        f"OCO placement skipped for {order.symbol}: Binance "
                        f"reports no open position — skipping individual SL/TP "
                        f"fallback (AC3 of #445, prevents -4509 churn)"
                    )
                elif oco_result.get("position_naked") or oco_result.get(
                    "requires_remediation"
                ):
                    # #504 (inverse of #482): partial OCO failure — one leg
                    # posted then was atomically cancelled, leaving the
                    # position fully naked. Blindly falling back to
                    # _place_individual_risk_orders here retries BOTH legs with
                    # the SAME uncorrected prices (the exact loop that produced
                    # the 2026-07-16 -2021 storm and ended every time with a
                    # naked position). Instead we STOP the same-price retry and
                    # hand off to the exchange-authoritative naked-position
                    # remediator, whose periodic loop iterates Binance
                    # positionRisk directly (independent of local state) and
                    # will re-arm with corrected pricing or flatten after the
                    # grace window. We do NOT mark the position closed — the
                    # position is open and unprotected, and stops-health must
                    # reflect that reality (AC3) rather than a false
                    # position_closed.
                    self.logger.error(
                        "🚨 NAKED POSITION (partial OCO failure) for "
                        f"{order.symbol} {oco_result.get('position_side')}: "
                        "skipping same-price individual-order retry; handing "
                        "off to naked-position remediator for re-arm/flatten "
                        f"(#504). oco_result={oco_result}"
                    )
                    # Intentionally do NOT record risk-order IDs (there are
                    # none) and do NOT mark the position closed. The position
                    # is open and unprotected; leaving it without SL/TP order
                    # IDs is what lets /positions/stops-health report it as
                    # naked (AC3) instead of a false position_closed. The
                    # exchange-authoritative remediator's periodic loop takes
                    # it from here.
                else:
                    self.logger.error(
                        f"❌ OCO ORDERS FAILED FOR {order.symbol}: {oco_result} - falling back to individual orders"
                    )
                    # Fallback to individual order placement
                    await self._place_individual_risk_orders(order, result)

            elif order.stop_loss and order.stop_loss > 0:
                # Only stop loss specified
                await self._place_stop_loss_order(order, result)
            elif order.take_profit and order.take_profit > 0:
                # Only take profit specified
                await self._place_take_profit_order(order, result)

        except Exception as e:
            self.logger.error(
                f"Failed to place risk management orders: {e}", exc_info=True
            )
            # Re-raise to trigger atomic rollback in caller
            raise

    async def _place_individual_risk_orders(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Fallback method to place individual SL/TP orders (non-OCO)"""
        try:
            # Place stop loss order if specified
            if order.stop_loss and order.stop_loss > 0:
                await self._place_stop_loss_order(order, result)

            # Place take profit order if specified
            if order.take_profit and order.take_profit > 0:
                await self._place_take_profit_order(order, result)
        except Exception as e:
            self.logger.error(
                f"Failed to place individual risk orders: {e}", exc_info=True
            )
            raise

    async def _place_stop_loss_order(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place stop loss order"""
        try:
            from contracts.order import OrderStatus, TradeOrder

            # Get the filled quantity with robust extraction
            filled_quantity = result.get("amount", None)

            # Convert to float if it's a string
            if filled_quantity is not None and isinstance(filled_quantity, str):
                try:
                    filled_quantity = float(filled_quantity)
                except (ValueError, TypeError):
                    filled_quantity = None

            # Use order.amount if result amount is 0, None, or invalid
            if filled_quantity is None or filled_quantity <= 0:
                filled_quantity = order.amount
                self.logger.info(
                    f"Using order.amount ({order.amount}) for SL - "
                    f"result amount was {result.get('amount')}"
                )

            # Safety check
            if filled_quantity <= 0:
                self.logger.error(
                    f"Cannot place stop loss: invalid quantity {filled_quantity}"
                )
                return

            # Validate and adjust stop loss price against PERCENT_PRICE filter if exchange is available
            adjusted_stop_loss = order.stop_loss
            if self.exchange and order.stop_loss:
                try:
                    (
                        is_adjusted,
                        adjusted_price,
                        adjustment_msg,
                    ) = await self.exchange.validate_and_adjust_price_for_percent_filter(
                        order.symbol, order.stop_loss, "STOP_LOSS"
                    )
                    # AC4 of #424: adjuster returns (False, None, reason) when
                    # the requested SL cannot satisfy both PERCENT_PRICE and
                    # the safety floor (e.g. SL would land within 6% of
                    # market). Emit a structured rejection and SKIP the SL
                    # placement so we don't ship a guaranteed-to-trigger stop.
                    if adjusted_price is None:
                        self.logger.error(
                            f"⛔ STOP LOSS REFUSED for {order.symbol}: {adjustment_msg}"
                        )
                        order.mark_rejected(
                            source="validation",
                            reason="sl_unreachable_within_filter",
                        )
                        await self._emit_execution_event_from_order(
                            order,
                            {"status": "rejected", "error": adjustment_msg},
                            event_type="rejected",
                            reason="sl_unreachable_within_filter",
                        )
                        return {
                            "status": "rejected",
                            "reason": "sl_unreachable_within_filter",
                            "rejection_source": "validation",
                            "error": adjustment_msg,
                        }
                    if is_adjusted:
                        self.logger.warning(
                            f"🔧 STOP LOSS PRICE ADJUSTED: {adjustment_msg}"
                        )
                        adjusted_stop_loss = adjusted_price
                except Exception as validation_error:
                    # Log validation error but continue (fail open)
                    self.logger.warning(
                        f"Failed to validate stop loss price for {order.symbol}: {validation_error}. "
                        f"Proceeding with original price."
                    )

            # Create stop loss order
            stop_loss_order = TradeOrder(
                order_id=f"sl_{order.order_id}_{datetime.now(UTC).timestamp()}",
                symbol=order.symbol,
                side=(
                    "sell" if order.side == "buy" else "buy"
                ),  # Opposite side to close position
                type="stop",  # Stop market order
                amount=filled_quantity,
                stop_loss=adjusted_stop_loss,
                take_profit=None,  # Not applicable for stop loss order
                target_price=None,  # Market order when triggered
                position_id=order.position_id,
                position_side=order.position_side,
                exchange=order.exchange,
                strategy_metadata=order.strategy_metadata,
                reduce_only=True,  # This is a position-closing order
                status=OrderStatus.PENDING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # Optional fields with defaults
                conditional_price=None,
                conditional_direction=None,
                conditional_timeout=None,
                iceberg_quantity=None,
                client_order_id=None,
                filled_amount=0.0,
                average_price=None,
                simulate=False,
                time_in_force="GTC",
                position_size_pct=None,
            )

            self.logger.info(
                f"📉 PLACING STOP LOSS: {order.symbol} {stop_loss_order.side} "
                f"{stop_loss_order.amount} @ {adjusted_stop_loss}"
            )

            # Execute stop loss order with retry and fallback logic
            if self.exchange:
                sl_result = await self._place_stop_loss_with_fallback(
                    stop_loss_order, order
                )
            else:
                sl_result = {"status": "error", "error": "No exchange configured"}

            # Check if order was successfully placed (NEW status for SL/TP orders)
            # SL/TP orders return "NEW" status when placed, not "filled"
            if sl_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                self.logger.info(
                    f"✅ STOP LOSS PLACED: {order.symbol} | "
                    f"Order ID: {sl_result.get('order_id', 'N/A')} | "
                    f"Stop Price: {sl_result.get('stop_price', order.stop_loss)} | "
                    f"Status: {sl_result.get('status')}"
                )

                # Track the stop loss order
                await self.order_manager.track_order(stop_loss_order, sl_result)

                # Update position record with stop loss order ID
                if order.position_id:
                    await self.position_manager.update_position_risk_orders(
                        order.position_id, stop_loss_order_id=sl_result.get("order_id")
                    )
            else:
                error_msg = f"STOP LOSS FAILED AFTER ALL RETRIES: {order.symbol} | Status: {sl_result.get('status', 'N/A')} | Error: {sl_result.get('error', 'Unknown error')}"
                self.logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

        except Exception as e:
            self.logger.error(f"Failed to place stop loss order: {e}", exc_info=True)
            raise

    async def _place_take_profit_order(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place take profit order"""
        try:
            from contracts.order import OrderStatus, TradeOrder

            # Get the filled quantity with robust extraction
            filled_quantity = result.get("amount", None)

            # Convert to float if it's a string
            if filled_quantity is not None and isinstance(filled_quantity, str):
                try:
                    filled_quantity = float(filled_quantity)
                except (ValueError, TypeError):
                    filled_quantity = None

            # Use order.amount if result amount is 0, None, or invalid
            if filled_quantity is None or filled_quantity <= 0:
                filled_quantity = order.amount
                self.logger.info(
                    f"Using order.amount ({order.amount}) for TP - "
                    f"result amount was {result.get('amount')}"
                )

            # Safety check
            if filled_quantity <= 0:
                self.logger.error(
                    f"Cannot place take profit: invalid quantity {filled_quantity}"
                )
                return

            # Validate and adjust take profit price against PERCENT_PRICE filter if exchange is available
            adjusted_take_profit = order.take_profit
            if self.exchange and order.take_profit:
                try:
                    (
                        is_adjusted,
                        adjusted_price,
                        adjustment_msg,
                    ) = await self.exchange.validate_and_adjust_price_for_percent_filter(
                        order.symbol, order.take_profit, "TAKE_PROFIT"
                    )
                    # AC4 of #424: the safety-floor refusal path also returns
                    # (False, None, reason). TPs don't trigger the safety
                    # floor by default (the floor is STOP-shaped only), but
                    # the new return shape requires handling None here too;
                    # skip TP placement in that case rather than crash.
                    if adjusted_price is None:
                        self.logger.error(
                            f"⛔ TAKE PROFIT REFUSED for {order.symbol}: {adjustment_msg}"
                        )
                        adjusted_take_profit = None
                    elif is_adjusted:
                        self.logger.warning(
                            f"🔧 TAKE PROFIT PRICE ADJUSTED: {adjustment_msg}"
                        )
                        adjusted_take_profit = adjusted_price
                except Exception as validation_error:
                    # Log validation error but continue (fail open)
                    self.logger.warning(
                        f"Failed to validate take profit price for {order.symbol}: {validation_error}. "
                        f"Proceeding with original price."
                    )

            # Create take profit order
            take_profit_order = TradeOrder(
                order_id=f"tp_{order.order_id}_{datetime.now(UTC).timestamp()}",
                symbol=order.symbol,
                side=(
                    "sell" if order.side == "buy" else "buy"
                ),  # Opposite side to close position
                type="take_profit",  # Take profit market order
                amount=filled_quantity,
                take_profit=adjusted_take_profit,
                target_price=None,  # Market order when triggered
                position_id=order.position_id,
                position_side=order.position_side,
                exchange=order.exchange,
                strategy_metadata=order.strategy_metadata,
                reduce_only=True,  # This is a position-closing order
                status=OrderStatus.PENDING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # Optional fields with defaults
                stop_loss=None,
                conditional_price=None,
                conditional_direction=None,
                conditional_timeout=None,
                iceberg_quantity=None,
                client_order_id=None,
                filled_amount=0.0,
                average_price=None,
                simulate=False,
                time_in_force="GTC",
                position_size_pct=None,
            )

            self.logger.info(
                f"📈 PLACING TAKE PROFIT: {order.symbol} {take_profit_order.side} "
                f"{take_profit_order.amount} @ {adjusted_take_profit}"
            )

            # Execute take profit order — with -2021 fallback strategy per #372.
            # Symmetric to _place_stop_loss_with_fallback: if the market has
            # already crossed the original TP, retry with the TP moved 1% then
            # 2% closer to entry before giving up.
            if self.exchange:
                tp_result = await self._place_take_profit_with_fallback(
                    take_profit_order, order
                )
            else:
                tp_result = {"status": "error", "error": "No exchange configured"}

            # Check if order was successfully placed (NEW status for SL/TP orders)
            # SL/TP orders return "NEW" status when placed, not "filled"
            if tp_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                # If the fallback adjusted the TP, surface the resolved price
                # so the log line matches the real on-exchange order rather
                # than the original (potentially stale) value.
                resolved_tp = tp_result.get("take_profit_price", order.take_profit)
                self.logger.info(
                    f"✅ TAKE PROFIT PLACED: {order.symbol} | "
                    f"Order ID: {tp_result.get('order_id', 'N/A')} | "
                    f"Take Profit Price: {resolved_tp} | "
                    f"Status: {tp_result.get('status')}"
                )

                # Track the take profit order
                await self.order_manager.track_order(take_profit_order, tp_result)

                # Update position record with take profit order ID
                if order.position_id:
                    await self.position_manager.update_position_risk_orders(
                        order.position_id,
                        take_profit_order_id=tp_result.get("order_id"),
                    )
            else:
                error_msg = f"TAKE PROFIT FAILED: {order.symbol} | Status: {tp_result.get('status', 'N/A')} | Error: {tp_result.get('error', 'Unknown error')}"
                self.logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

        except Exception as e:
            self.logger.error(f"Failed to place take profit order: {e}", exc_info=True)
            raise

    async def _fetch_binance_position_qty(
        self, symbol: str, position_side: str | None
    ) -> float:
        """Return |positionAmt| for (symbol, position_side) from Binance.

        Best-effort: returns 0.0 when the exchange has no get_position_info,
        the call raises, or the position is absent. Never raises into the
        caller — this is invoked from the rollback fallback where the
        observable contract is "give me a number; zero means skip".
        """
        if self.exchange is None or not hasattr(self.exchange, "get_position_info"):
            return 0.0
        try:
            raw = await self.exchange.get_position_info()
        except Exception:
            self.logger.warning(
                "get_position_info() failed during atomic-rollback fallback",
                exc_info=True,
            )
            return 0.0
        if not raw:
            return 0.0
        target_side = (position_side or "").upper()
        for pos in raw:
            if pos.get("symbol") != symbol:
                continue
            side = str(pos.get("positionSide", "BOTH")).upper()
            try:
                raw_amt = float(pos.get("positionAmt", 0))
            except (TypeError, ValueError):
                continue
            # ONE-WAY mode returns positionSide="BOTH"; derive the effective
            # side from the sign of positionAmt (matches position_reconciler's
            # _normalise_side). A LONG rollback against a BOTH row with
            # negative positionAmt would otherwise send a sell reduceOnly on
            # the wrong direction.
            if side == "BOTH":
                if raw_amt == 0:
                    continue
                effective_side = "LONG" if raw_amt > 0 else "SHORT"
                if target_side and effective_side != target_side:
                    continue
            elif side in ("LONG", "SHORT"):
                if target_side and side != target_side:
                    continue
            qty = abs(raw_amt)
            if qty > 0:
                return qty
        return 0.0

    async def _exchange_position_presence(
        self, symbol: str, position_side: str
    ) -> bool | None:
        """Best-effort answer to "does the exchange hold this position?".

        Returns ``True`` when a matching ``(symbol, position_side)`` position
        exists, ``False`` when the authoritative ExchangeTruthStore is ready
        and confidently reports its absence, and ``None`` when the answer is
        unknown (store not ready / not wired) — in which case callers must NOT
        block, to avoid skipping a legitimate close (#481 AC3).

        Only the truth store yields a definitive ``False``: the REST helper
        ``_fetch_binance_position_qty`` returns ``0.0`` for both "absent" and
        "lookup failed", so a REST zero is treated as unknown here rather than
        risk suppressing a real close on a transient API error.
        """
        store = getattr(getattr(self, "user_data_consumer", None), "store", None)
        if store is not None and getattr(store, "is_ready", False):
            positions = store.get_positions()
            return _has_matching_exchange_position(
                {"symbol": symbol, "side": position_side}, positions
            )
        return None

    async def close_position_with_cleanup(
        self,
        position_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str = "manual",
        cio_audited: bool = False,
    ) -> dict[str, Any]:
        """
        Close a position and clean up all associated SL/TP orders (OCO cleanup)

        Args:
            position_id: Unique identifier for the position
            symbol: Trading symbol (e.g., BTCUSDT)
            position_side: LONG or SHORT
            quantity: Position size to close
            reason: Reason for closing (manual, sl_triggered, tp_triggered, etc.)

        Returns:
            Dict with closing result and cleanup status
        """
        try:
            self.logger.info("🔄 CLOSING POSITION WITH OCO CLEANUP")
            self.logger.info(f"Position ID: {position_id}")
            self.logger.info(f"Symbol: {symbol}")
            self.logger.info(f"Position Side: {position_side}")
            self.logger.info(f"Quantity: {quantity}")
            self.logger.info(f"Reason: {reason}")

            # Step 1: Cancel associated OCO orders first
            oco_cancelled = False
            if position_id in self.oco_manager.active_oco_pairs:
                self.logger.info(f"🔄 CANCELLING OCO ORDERS FOR POSITION {position_id}")
                oco_cancelled = await self.oco_manager.cancel_oco_pair(position_id)
                if oco_cancelled:
                    self.logger.info("✅ OCO ORDERS CANCELLED SUCCESSFULLY")
                else:
                    self.logger.warning("⚠️  FAILED TO CANCEL OCO ORDERS")

            # Step 1b (#481 AC3): do not emit a close order for a position the
            # exchange no longer holds. The 2026-06-18 thrash loop fired
            # reduceOnly closes against ghost strategy positions with no
            # exchange counterpart. Stale OCO legs are still cancelled above
            # (harmless cleanup); only the wasteful MARKET close is suppressed.
            if os.getenv("TE_CLOSE_GUARD_ENABLED", "1") == "1":
                presence = await self._exchange_position_presence(symbol, position_side)
                if presence is False:
                    self.logger.warning(
                        "⛔ CLOSE BLOCKED (#481 AC3): exchange reports no open "
                        "%s position for %s — refusing to emit reduceOnly close "
                        "(reason=%s). Ghost strategy row will be evicted by the "
                        "#480 reconciler.",
                        position_side,
                        symbol,
                        reason,
                    )
                    strategy_close_blocked_no_exchange_position_total.labels(
                        symbol=symbol, side=position_side
                    ).inc()
                    return {
                        "position_closed": False,
                        "oco_cancelled": oco_cancelled,
                        "close_result": None,
                        "status": "skipped_no_exchange_position",
                    }

            # Step 1c (#481 AC5): fail-safe thrash circuit-breaker. Block a
            # close when un-audited closes on this symbol have exceeded the
            # per-window cap (default 2 in 10 min). Audited closes (a close
            # tied to a CIO decision) never trip or get blocked by the breaker.
            if not cio_audited and self.thrash_breaker.should_block(symbol):
                self.logger.error(
                    "🛑 THRASH CIRCUIT OPEN (#481 AC5): %s exceeded %d un-audited "
                    "close(s) within %d min — blocking further churn "
                    "(reason=%s).",
                    symbol,
                    self.thrash_breaker.max_cycles,
                    self.thrash_breaker.window_minutes,
                    reason,
                )
                dispatcher_thrash_circuit_open_total.labels(symbol=symbol).inc()
                return {
                    "position_closed": False,
                    "oco_cancelled": oco_cancelled,
                    "close_result": None,
                    "status": "skipped_thrash_circuit_open",
                }
            # Record the allowed close so it counts toward the window.
            self.thrash_breaker.record_close(symbol, cio_audited=cio_audited)

            # Step 2: Close the position
            position_closed = False
            try:
                # Determine order side for closing (opposite of position)
                close_side = "sell" if position_side == "LONG" else "buy"

                # Create closing order
                close_order = TradeOrder(
                    symbol=symbol,
                    side=close_side,
                    type=OrderType.MARKET,
                    amount=quantity,
                    target_price=None,
                    stop_loss=None,
                    take_profit=None,
                    conditional_price=None,
                    conditional_direction=None,
                    conditional_timeout=None,
                    iceberg_quantity=None,
                    client_order_id=None,
                    order_id=f"close_{position_id}_{int(time.time())}",
                    status=OrderStatus.PENDING,
                    filled_amount=0.0,
                    average_price=None,
                    position_id=None,
                    position_side=position_side,
                    exchange="binance",
                    reduce_only=True,  # This is a position-closing order
                    time_in_force=TimeInForce.GTC,
                    position_size_pct=None,
                    simulate=False,
                    updated_at=None,
                )

                self.logger.info(
                    f"📤 CLOSING POSITION: {symbol} {close_side} {quantity}"
                )

                # Execute closing order
                if self.exchange:
                    close_result = await self.exchange.execute(close_order)
                else:
                    raise ValueError("Exchange not configured")

                if close_result.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
                    position_closed = True
                    self.logger.info("✅ POSITION CLOSED SUCCESSFULLY")
                    self.logger.info(f"  Order ID: {close_result.get('order_id')}")
                    self.logger.info(f"  Status: {close_result.get('status')}")
                else:
                    self.logger.error(f"❌ FAILED TO CLOSE POSITION: {close_result}")

            except Exception as e:
                self.logger.error(f"❌ ERROR CLOSING POSITION: {e}")

            # Step 3: Clean up position record
            try:
                if position_id:
                    await self.position_manager.close_position_record(
                        position_id, {"reason": reason, "manual_close": True}
                    )
                    self.logger.info("✅ POSITION RECORD UPDATED")
            except Exception as e:
                self.logger.error(f"❌ ERROR UPDATING POSITION RECORD: {e}")

            return {
                "position_closed": position_closed,
                "oco_cancelled": oco_cancelled,
                "close_result": close_result if position_closed else None,
                "status": "success" if position_closed else "failed",
            }

        except Exception as e:
            self.logger.error(
                f"❌ ERROR IN POSITION CLOSE WITH CLEANUP: {e}", exc_info=True
            )
            return {
                "position_closed": False,
                "oco_cancelled": False,
                "close_result": None,
                "status": "error",
                "error": str(e),
            }

    def _generate_signal_fingerprint(self, signal: Signal) -> str:
        """
        Generate a unique fingerprint for a signal to prevent duplicate processing
        across multiple replicas.

        The fingerprint includes:
        - Signal ID (if available)
        - Strategy ID
        - Symbol
        - Action (buy/sell)
        - Price (rounded to avoid floating point differences)
        - Timestamp (rounded to second to catch same-second duplicates)

        Args:
            signal: The trading signal

        Returns:
            A unique fingerprint string for distributed locking
        """
        import hashlib

        # Use signal_id if available (preferred)
        if signal.signal_id:
            return f"{signal.signal_id}_{signal.symbol}"

        # Fallback: Generate fingerprint from signal properties
        # Round price to 2 decimals to avoid floating point differences
        price_str = f"{signal.price:.2f}" if signal.price else "0"

        # Round timestamp to nearest second
        timestamp_str = ""
        if signal.timestamp:
            timestamp_str = f"{int(signal.timestamp.timestamp())}"

        # Combine key signal properties
        fingerprint_data = (
            f"{signal.strategy_id}|"
            f"{signal.symbol}|"
            f"{signal.action}|"
            f"{price_str}|"
            f"{timestamp_str}"
        )

        # Generate hash for shorter lock key
        fingerprint_hash = hashlib.sha256(fingerprint_data.encode()).hexdigest()[:12]

        return f"{signal.strategy_id}_{signal.symbol}_{fingerprint_hash}"

    async def _place_stop_loss_with_fallback(
        self, stop_loss_order: TradeOrder, original_order: TradeOrder
    ) -> dict[str, Any]:
        """
        Place stop loss order with fallback strategies for API errors

        Fallback strategy for APIError -2021 (Order would immediately trigger):
        1. Try original stop loss price
        2. If fails, adjust price to be safer (further from market)
        3. If still fails, place at market price with stop limit

        Args:
            stop_loss_order: The stop loss order to place
            original_order: The original position entry order

        Returns:
            Result dictionary with status and order details
        """
        try:
            # Attempt 1: Try with original stop loss price
            self.logger.info(
                f"🔄 Attempt 1: Placing SL at original price {original_order.stop_loss}"
            )
            from typing import cast

            sl_result = cast(
                dict[str, Any], await self.exchange.execute(stop_loss_order)
            )

            if sl_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                sl_result["stop_price"] = original_order.stop_loss
                return sl_result

            # Check if it's the "would immediately trigger" error
            error_msg = str(sl_result.get("error", ""))
            if "-2021" in error_msg or "immediately trigger" in error_msg.lower():
                self.logger.warning(
                    f"⚠️  SL order would immediately trigger at {original_order.stop_loss}. "
                    f"Attempting fallback strategies..."
                )

                # Attempt 2: Adjust stop loss price to be safer (1% further from entry)
                # For LONG: Move SL down (lower price)
                # For SHORT: Move SL up (higher price)
                entry_price = (
                    original_order.target_price or original_order.stop_loss or 0
                )
                if entry_price <= 0:
                    self.logger.error(
                        "Cannot calculate adjusted SL: invalid entry price"
                    )
                    return sl_result

                is_long = original_order.side == "buy"
                if is_long:
                    # For LONG, move SL down by 1%
                    adjusted_sl = float(original_order.stop_loss) * 0.99
                else:
                    # For SHORT, move SL up by 1%
                    adjusted_sl = float(original_order.stop_loss) * 1.01

                self.logger.info(
                    f"🔄 Attempt 2: Placing SL at adjusted price {adjusted_sl} "
                    f"(original: {original_order.stop_loss})"
                )

                # Update stop loss order with adjusted price
                stop_loss_order.stop_loss = adjusted_sl
                sl_result = cast(
                    dict[str, Any], await self.exchange.execute(stop_loss_order)
                )

                if sl_result.get("status") in [
                    "filled",
                    "partially_filled",
                    "pending",
                    "NEW",
                ]:
                    self.logger.warning(
                        f"✅ SL placed at adjusted price {adjusted_sl} "
                        f"(moved from {original_order.stop_loss})"
                    )
                    sl_result["stop_price"] = adjusted_sl
                    return sl_result

                # Attempt 3: Use a wider adjustment (2% from entry)
                if is_long:
                    adjusted_sl = float(original_order.stop_loss) * 0.98
                else:
                    adjusted_sl = float(original_order.stop_loss) * 1.02

                self.logger.info(
                    f"🔄 Attempt 3: Placing SL at wider adjusted price {adjusted_sl}"
                )

                stop_loss_order.stop_loss = adjusted_sl
                sl_result = cast(
                    dict[str, Any], await self.exchange.execute(stop_loss_order)
                )

                if sl_result.get("status") in [
                    "filled",
                    "partially_filled",
                    "pending",
                    "NEW",
                ]:
                    self.logger.warning(
                        f"✅ SL placed at wider adjusted price {adjusted_sl} "
                        f"(moved from {original_order.stop_loss})"
                    )
                    sl_result["stop_price"] = adjusted_sl
                    return sl_result

                # If all attempts fail, log critical warning but return result
                self.logger.error(
                    f"❌ CRITICAL: All SL placement attempts failed for {original_order.symbol}. "
                    f"Position is NOT PROTECTED by stop loss!"
                )
                return sl_result

            # For other errors, return the original result
            return sl_result

        except Exception as e:
            self.logger.error(f"❌ Exception in SL fallback logic: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _place_take_profit_with_fallback(
        self, take_profit_order: TradeOrder, original_order: TradeOrder
    ) -> dict[str, Any]:
        """
        Place take profit order with fallback strategies for API errors (per #372).

        Mirrors the SL fallback pattern. Fallback strategy for APIError -2021
        (Order would immediately trigger), which happens when fast market moves
        have already crossed the planned TP before placement:

        1. Try the original TP price.
        2. If -2021, adjust TP 1% CLOSER to entry (LONG: lower TP; SHORT: higher TP).
        3. If still -2021, adjust TP 2% closer to entry.
        4. Cap at 2 adjustments — if all fail, return the last result so the caller
           can decide whether to leave the position without a TP.

        Args:
            take_profit_order: The take profit order to place.
            original_order: The original position entry order (provides side + entry
                target_price for context).

        Returns:
            Result dictionary with status and order details. On successful adjusted
            placement, the dict is annotated with `take_profit_price`.
        """
        try:
            from typing import cast

            self.logger.info(
                f"🔄 Attempt 1: Placing TP at original price {take_profit_order.take_profit}"
            )
            tp_result = cast(
                dict[str, Any], await self.exchange.execute(take_profit_order)
            )

            if tp_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                tp_result["take_profit_price"] = take_profit_order.take_profit
                return tp_result

            error_msg = str(tp_result.get("error", ""))
            if "-2021" in error_msg or "immediately trigger" in error_msg.lower():
                self.logger.warning(
                    f"⚠️  TP order would immediately trigger at "
                    f"{take_profit_order.take_profit}. Attempting fallback strategies..."
                )

                original_tp = take_profit_order.take_profit
                if original_tp is None or float(original_tp) <= 0:
                    self.logger.error(
                        "Cannot calculate adjusted TP: invalid original TP price"
                    )
                    return tp_result

                # is_long indicates the ENTRY side. TP for a LONG entry is ABOVE
                # entry; if the market has rallied past it (would immediately
                # trigger as filled), we adjust TP DOWN (closer to entry).
                # Symmetrically: TP for a SHORT entry is BELOW entry; if the
                # market has fallen past it, we adjust TP UP (closer to entry).
                is_long = original_order.side == "buy"

                # Attempt 2: 1% adjustment toward entry.
                if is_long:
                    adjusted_tp = float(original_tp) * 0.99
                else:
                    adjusted_tp = float(original_tp) * 1.01

                self.logger.info(
                    f"🔄 Attempt 2: Placing TP at adjusted price {adjusted_tp} "
                    f"(original: {original_tp})"
                )

                take_profit_order.take_profit = adjusted_tp
                tp_result = cast(
                    dict[str, Any], await self.exchange.execute(take_profit_order)
                )

                if tp_result.get("status") in [
                    "filled",
                    "partially_filled",
                    "pending",
                    "NEW",
                ]:
                    self.logger.warning(
                        f"✅ TP placed at adjusted price {adjusted_tp} "
                        f"(moved from {original_tp})"
                    )
                    tp_result["take_profit_price"] = adjusted_tp
                    return tp_result

                # Attempt 3: 2% adjustment toward entry (cap per #372 AC).
                if is_long:
                    adjusted_tp = float(original_tp) * 0.98
                else:
                    adjusted_tp = float(original_tp) * 1.02

                self.logger.info(
                    f"🔄 Attempt 3: Placing TP at wider adjusted price {adjusted_tp}"
                )

                take_profit_order.take_profit = adjusted_tp
                tp_result = cast(
                    dict[str, Any], await self.exchange.execute(take_profit_order)
                )

                if tp_result.get("status") in [
                    "filled",
                    "partially_filled",
                    "pending",
                    "NEW",
                ]:
                    self.logger.warning(
                        f"✅ TP placed at wider adjusted price {adjusted_tp} "
                        f"(moved from {original_tp})"
                    )
                    tp_result["take_profit_price"] = adjusted_tp
                    return tp_result

                # All attempts failed — position will be SL-protected but
                # un-take-profited until a downstream re-attempt fires.
                self.logger.error(
                    f"❌ All TP placement attempts failed for {original_order.symbol}. "
                    f"Position is left without a take-profit order; SL still active."
                )
                return tp_result

            # For non -2021 errors, surface the original result unchanged so the
            # caller can distinguish "TP didn't fit the market" from "TP failed
            # for another reason" (e.g., margin, lot-size, account state).
            return tp_result

        except Exception as e:
            self.logger.error(f"❌ Exception in TP fallback logic: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown dispatcher and stop OCO monitoring"""
        try:
            self.logger.info("🔄 SHUTTING DOWN DISPATCHER")

            # Stop OCO monitoring
            await self.oco_manager.stop_monitoring()

            self.logger.info("✅ DISPATCHER SHUTDOWN COMPLETE")

        except Exception as e:
            self.logger.error(f"❌ ERROR DURING SHUTDOWN: {e}")
