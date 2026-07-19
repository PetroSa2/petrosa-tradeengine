"""
Position Tracking Metrics - hedge mode position tracking.

⚠️ CRITICAL: These metrics follow the exact same pattern as existing metrics in
dispatcher.py, api.py, and consumer.py. DO NOT modify the pattern or it will
break the observability stack.

Dual-export (per #415): every business metric below is a `prometheus_client`
instrument exposed via the Prometheus scrape endpoint (pull model). In addition,
a parallel set of OTel SDK instruments (see the "OTel SDK instruments" section
at the bottom of this module) is registered against the `MeterProvider` wired by
`petrosa_otel.setup_telemetry()`, so the same business metrics also flow via the
OTLP push pipeline to Grafana Alloy. The prometheus_client path is unchanged.
"""

from petrosa_otel import get_meter
from prometheus_client import Counter, Gauge, Histogram

# Position Lifecycle Metrics
positions_opened_total = Counter(
    "tradeengine_positions_opened_total",
    "Total positions opened",
    ["strategy_id", "symbol", "position_side", "exchange"],
)

positions_closed_total = Counter(
    "tradeengine_positions_closed_total",
    "Total positions closed",
    ["strategy_id", "symbol", "position_side", "close_reason", "exchange"],
)

# Performance Metrics (Money Terms)
position_pnl_usd = Histogram(
    "tradeengine_position_pnl_usd",
    "Position PnL in USD",
    ["strategy_id", "symbol", "position_side", "exchange"],
    buckets=[-1000, -500, -100, -50, -10, 0, 10, 50, 100, 500, 1000, 5000],
)

position_pnl_percentage = Histogram(
    "tradeengine_position_pnl_percentage",
    "Position PnL as percentage",
    ["strategy_id", "symbol", "position_side", "exchange"],
    buckets=[-50, -20, -10, -5, -2, 0, 2, 5, 10, 20, 50, 100],
)

position_duration_seconds = Histogram(
    "tradeengine_position_duration_seconds",
    "Position duration in seconds",
    ["strategy_id", "symbol", "position_side", "close_reason", "exchange"],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400],  # 1m to 1day
)

position_roi = Histogram(
    "tradeengine_position_roi",
    "Position Return on Investment",
    ["strategy_id", "symbol", "position_side", "exchange"],
    buckets=[-0.5, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2, 0.5, 1.0],
)

# Real-time Position Value
open_positions_value_usd = Gauge(
    "tradeengine_open_positions_value_usd",
    "Total value of open positions in USD",
    ["strategy_id", "exchange"],
)

unrealized_pnl_usd = Gauge(
    "tradeengine_unrealized_pnl_usd",
    "Unrealized PnL for open positions",
    ["strategy_id", "symbol", "position_side", "exchange"],
)

# Win Rate Tracking
positions_winning_total = Counter(
    "tradeengine_positions_winning_total",
    "Total winning positions (PnL > 0)",
    ["strategy_id", "symbol", "position_side", "exchange"],
)

positions_losing_total = Counter(
    "tradeengine_positions_losing_total",
    "Total losing positions (PnL < 0)",
    ["strategy_id", "symbol", "position_side", "exchange"],
)

# Commission Tracking
position_commission_usd = Histogram(
    "tradeengine_position_commission_usd",
    "Total commission paid per position in USD",
    ["strategy_id", "symbol", "exchange"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100],
)

# Position Entry/Exit Prices (for monitoring)
position_entry_price = Histogram(
    "tradeengine_position_entry_price_usd",
    "Position entry price in USD",
    ["symbol", "position_side", "exchange"],
    buckets=[1, 10, 100, 1000, 10000, 50000, 100000],
)

position_exit_price = Histogram(
    "tradeengine_position_exit_price_usd",
    "Position exit price in USD",
    ["symbol", "position_side", "exchange"],
    buckets=[1, 10, 100, 1000, 10000, 50000, 100000],
)

# ========================================
# Strategy OCO Attribution Metrics
# ========================================

strategy_oco_placed_total = Counter(
    "tradeengine_strategy_oco_placed_total",
    "Total OCO pairs placed per strategy",
    ["strategy_id", "symbol", "exchange"],
)

strategy_tp_triggered_total = Counter(
    "tradeengine_strategy_tp_triggered_total",
    "Strategy's own TP order triggered",
    ["strategy_id", "symbol", "exchange"],
)

strategy_sl_triggered_total = Counter(
    "tradeengine_strategy_sl_triggered_total",
    "Strategy's own SL order triggered",
    ["strategy_id", "symbol", "exchange"],
)

strategy_pnl_realized = Histogram(
    "tradeengine_strategy_pnl_realized",
    "Realized P&L per strategy exit",
    ["strategy_id", "close_reason", "exchange"],
    buckets=[-100, -50, -10, -5, -1, 0, 1, 5, 10, 50, 100, 500],
)

active_oco_pairs_per_position = Gauge(
    "tradeengine_active_oco_pairs_per_position",
    "Number of active OCO pairs per exchange position",
    ["symbol", "position_side", "exchange"],
)

# #425 (RC#1 of #424) + #482 AC2: orphan leg from partial OCO failure.
# Incremented every time one leg posts and the counterpart fails. The
# cancel_outcome label distinguishes between (a) we cancelled the orphan
# cleanly (cancel_outcome="success") and (b) the surviving-leg cancel itself
# errored so the position remains unhedged on Binance (cancel_outcome="failed").
# Operators alert on the failed bucket; success-bucket counts are visibility
# into how often the OCO path is non-atomic at the exchange.
oco_orphan_leg_total = Counter(
    "petrosa_tradeengine_oco_orphan_leg_total",
    "OCO partial-failure events split by surviving-leg cancel outcome",
    ["symbol", "side", "leg", "cancel_outcome"],
)

# #426 (RC#2 of #424): the atomic-rollback path itself failed — the
# OCO-failure cleanup could not close the position on Binance, so the
# position remains unhedged. Paired with the
# alerts.tradeengine.rollback_failed.<symbol> NATS alert.
atomic_rollback_failed_total = Counter(
    "petrosa_tradeengine_atomic_rollback_failed_total",
    "Atomic-rollback failures during OCO-failure cleanup (position left unhedged)",
    ["symbol", "reason"],
)

# #448 — position write failures after retry exhaustion; paired with the
# alerts.tradeengine.persist_failed.<symbol> NATS alert.
position_persist_failed_total = Counter(
    "petrosa_tradeengine_position_persist_failed_total",
    "Position write failures after retry exhaustion (position may diverge from Binance state)",
    ["symbol", "position_side", "operation", "reason"],
)

# #480 — StrategyPositionManager ghost evictions. A strategy position is a
# "ghost" when StrategyPositionManager has it open but no matching position
# exists in the ExchangeTruthStore. The reconciler removes ghosts older than
# the min-age threshold and increments this counter per eviction.
strategy_position_ghost_evicted_total = Counter(
    "petrosa_tradeengine_strategy_position_ghost_evicted_total",
    "Strategy positions evicted as ghosts (no matching exchange position)",
    ["symbol", "side", "reason"],
)

# #480 — Strategy-layer reconciliation runs. Result label:
# "ok"                      — pass completed (eviction count is on the ghost counter)
# "error"                   — exception during pass
# "skipped_store_not_ready" — ExchangeTruthStore not ready yet (post-boot grace)
strategy_position_reconcile_runs_total = Counter(
    "petrosa_tradeengine_strategy_position_reconcile_runs_total",
    "Strategy-layer reconciliation runs",
    ["result"],
)

# #480 — current number of strategy positions with no matching exchange
# position (pre-eviction view, refreshed each pass). Useful for alerting when
# ghosts accumulate faster than the reconciler can age them out.
strategy_position_ghost_gauge = Gauge(
    "petrosa_tradeengine_strategy_position_ghost_count",
    "Current count of strategy positions with no matching exchange position",
)

# #481 AC3 — close-order emission blocked because the exchange has no matching
# position. The 2026-06-18 testnet thrash loop fired reduceOnly closes against
# ghost strategy positions with no exchange counterpart. This is the
# emission-time guard (defense-in-depth to the #480 reconciler that evicts the
# ghost rows out-of-band): before firing a MARKET reduceOnly close,
# close_position_with_cleanup consults the ExchangeTruthStore and skips when it
# confidently reports no matching (symbol, side) position.
strategy_close_blocked_no_exchange_position_total = Counter(
    "petrosa_tradeengine_strategy_close_blocked_no_exchange_position_total",
    "Close-order emissions blocked because no matching exchange position exists",
    ["symbol", "side"],
)

# #481 AC5 — thrash circuit-breaker openings. Fail-safe rate limit: no more than
# N (default 2) close emissions on the same symbol within M (default 10) minutes
# lacking a CIO-decision audit trail. When the threshold is crossed the breaker
# opens and further un-audited closes on that symbol are blocked until the
# window clears, ticking this counter each time it blocks.
dispatcher_thrash_circuit_open_total = Counter(
    "petrosa_tradeengine_dispatcher_thrash_circuit_open_total",
    "Close emissions blocked by the open/close thrash circuit-breaker",
    ["symbol"],
)

# #497 — current OCO orphan count: positions on Binance that lack the full
# stop/target pair (i.e. unhedged). The pre-existing
# `tradeengine-oco-pair-orphan` Grafana alert fires on
# ``sum(petrosa_tradeengine_oco_orphan_count) > 0`` but the metric was absent
# from Grafana Cloud (no OTel export). Gauge so it can go back to 0 when the
# orphan is resolved.
oco_orphan_count = Gauge(
    "petrosa_tradeengine_oco_orphan_count",
    "Current number of OCO orphans: positions on Binance that lack their full stop/target pair",
)

# #972 — OCO pair age: seconds since an OCO pair entered ``active_oco_pairs``.
# An OCO pair that stays active for a long time (>300s) signals a stuck monitor
# loop, a silently-failed cancellation, or a race. The gauge is set every
# ``_monitor_orders`` cycle (2s cadence) and the (symbol, position_side) series
# is removed when the pair completes/cancels so a stale pair never reports a
# frozen-but-climbing age. Dual-exported (see ``otel_oco_pair_age_seconds``
# below, per #415/#497) so the Grafana Cloud OTLP datasource sees it too.
oco_pair_age_seconds = Gauge(
    "petrosa_tradeengine_oco_pair_age_seconds",
    "Age in seconds of each active OCO pair (time since it entered active_oco_pairs)",
    ["symbol", "position_side"],
)

# #484 — stops-health violation alarms. The 2026-06-18 testnet diagnosis showed
# violation_count=12 with alarms_emitted=0: alarms only fired on the force-close
# branch, so a "successful" (or lying) remediation silenced the operator-facing
# alert. Every violation now raises exactly one alarm on the
# alerts.tradeengine.> NATS path (AlertsConsumer -> Telegram, petrosa_k8s#810)
# regardless of remediation_outcome. reason: missing_sl|missing_tp|both|stale_order_id.
#   - emitted:    alarm published successfully to NATS (delivered).
#   - suppressed: alarm raised but NOT delivered (NATS disabled/unavailable) so
#                 the silencing is operator-visible instead of a silent drop.
# emitted + suppressed partition every violation, so a dashboard can derive
# violation_count = sum(emitted)+sum(suppressed) and the gap = suppressed with
# zero emitted. Prometheus-only, consistent with the #480/#481 counters above.
stops_health_alarm_emitted_total = Counter(
    "petrosa_tradeengine_stops_health_alarm_emitted_total",
    "Stops-health violation alarms delivered to the NATS alerts.> path",
    ["reason"],
)

stops_health_alarm_suppressed_total = Counter(
    "petrosa_tradeengine_stops_health_alarm_suppressed_total",
    "Stops-health violation alarms raised but not delivered (NATS disabled/unavailable)",
    ["reason"],
)

# ========================================
# Business Metrics for Trade Execution Monitoring
# ========================================

# Order Execution Metrics
orders_executed_by_type = Counter(
    "tradeengine_orders_executed_by_type_total",
    "Total orders executed by type (market, limit, stop, etc.)",
    ["order_type", "side", "symbol", "exchange"],
)

order_execution_latency_seconds = Histogram(
    "tradeengine_order_execution_latency_seconds",
    "Time from signal receipt to order execution completion",
    ["symbol", "order_type", "exchange"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],  # 100ms to 2min
)

# Risk Management Metrics
risk_rejections_total = Counter(
    "tradeengine_risk_rejections_total",
    "Total orders rejected by risk management",
    ["reason", "symbol", "exchange"],
)

risk_checks_total = Counter(
    "tradeengine_risk_checks_total",
    "Total risk checks performed",
    ["check_type", "result", "exchange"],
)

# Position Size Monitoring
current_position_size = Gauge(
    "tradeengine_current_position_size",
    "Current position size by symbol and side",
    ["symbol", "position_side", "exchange"],
)

total_position_value_usd = Gauge(
    "tradeengine_total_position_value_usd",
    "Total value of all positions in USD",
    ["exchange"],
)

# PnL Monitoring (Aggregate Metrics)
total_realized_pnl_usd = Gauge(
    "tradeengine_total_realized_pnl_usd",
    "Cumulative realized PnL in USD (aggregate across all positions)",
    ["exchange"],
)

total_unrealized_pnl_usd = Gauge(
    "tradeengine_total_unrealized_pnl_usd",
    "Total unrealized PnL in USD (aggregate across all open positions)",
    ["exchange"],
)

total_daily_pnl_usd = Gauge(
    "tradeengine_total_daily_pnl_usd",
    "Total daily PnL in USD (resets at midnight UTC)",
    ["exchange"],
)

# Order Success Metrics
order_success_rate = Gauge(
    "tradeengine_order_success_rate",
    "Ratio of successful orders to total orders",
    ["symbol", "order_type", "exchange"],
)

order_failures_total = Counter(
    "tradeengine_order_failures_total",
    "Total order execution failures",
    ["symbol", "order_type", "failure_reason", "exchange"],
)

order_placement_skipped_total = Counter(
    "tradeengine_order_placement_skipped_total",
    "Protective order placements skipped because exchange already has an armed order",
    ["reason"],
)

exchange_truth_shadow_delta_total = Counter(
    "tradeengine_exchange_truth_shadow_delta_total",
    "Divergences detected between local registry and ExchangeTruthStore in shadow mode",
    ["symbol", "side", "field"],
)

# Per #483: outcome of -4130 ("already existing") retry reconciliation against
# /openOrders + /openAlgoOrders truth. Outcomes:
#   - already_protected: matching closePosition stop/TP found → no retry, early success.
#   - retry_succeeded:   no match found → fell through to backoff retry → succeeded.
#   - retry_failed:      no match found → fell through to backoff retry → all attempts failed.
#   - conflicting_order: a non-matching order occupies the slot; logged for investigation.
binance_4130_resolution_total = Counter(
    "tradeengine_binance_4130_resolution_total",
    "Outcome of -4130 'already existing' retry reconciliation against /openOrders + /openAlgoOrders truth",
    ["outcome", "symbol"],
)

# Per #490: outcome of startup orphaned algo-order (closePosition TP/SL)
# cancellation. Orphans are discovered via /openAlgoOrders (disjoint from
# /openOrders, #483) and must be cancelled with algoId, not orderId (the
# latter yields APIError(-1102)). Outcomes:
#   - succeeded:       algo order cancelled via the Algo Order DELETE endpoint.
#   - not_found_4029:  -4029 "order does not exist" → already cancelled
#                      out-of-band; treated as success, logged INFO not ERROR.
#   - failed_other:    any other failure (network, signature, unexpected code).
orphan_algo_cancel_total = Counter(
    "tradeengine_orphan_algo_cancel_total",
    "Outcome of startup orphaned algo-order (closePosition TP/SL) cancellation",
    ["outcome", "symbol"],
)

# ========================================
# NATS Heartbeat & Fail-Safe Observability
# ========================================

last_heartbeat_received_timestamp = Gauge(
    "tradeengine_heartbeat_last_received_timestamp",
    "Unix timestamp of the last valid heartbeat received from CIO",
    ["service", "subject"],
)

restricted_mode_status = Gauge(
    "tradeengine_restricted_mode_status",
    "Binary status of RESTRICTED_MODE (1 = Restricted, 0 = Normal)",
)

# #500 — effective naked-position remediation mode, exported as a labeled gauge
# so operators can alert when the watchdog is running detection-only ("off").
# Exactly one series is set to 1 (the active mode); all other mode series are 0.
# The 2026-07-16 incident showed the remediator silently ran "off" in prod
# (TE_NAKED_POSITION_REMEDIATION_MODE unset) — detecting naked positions but
# never re-arming/flattening. This metric makes the effective mode first-class
# and drives the `tradeengine-naked-remediation-off` alert.
#   arm_or_flatten enforcement:  ...{mode="arm_or_flatten"} == 1
#   detection-only (unsafe):     ...{mode="off"} == 1
naked_position_remediation_mode_status = Gauge(
    "tradeengine_naked_position_remediation_mode_status",
    "Effective naked-position remediation mode (1 = active mode; one series per "
    "mode set to 1, others 0). Alert when {mode='off'} == 1.",
    ["mode"],
)

# All recognized modes — used to zero out inactive series so a mode transition
# does not leave a stale `1` on the previous mode label.
_NAKED_REMEDIATION_MODES = ("off", "dry_run", "arm_only", "arm_or_flatten")


def set_naked_position_remediation_mode(mode: str) -> None:
    """Set the effective naked-position remediation mode gauge (#500).

    Sets the active mode's series to 1 and every other known mode's series to 0
    so alerting on ``{mode="off"} == 1`` is unambiguous. Unknown/garbage modes
    are coerced to ``off`` (matching NakedPositionRemediator._coerce_mode) so a
    misconfigured env still surfaces as the unsafe detection-only state rather
    than silently disappearing.
    """
    normalized = (mode or "off").lower().strip()
    if normalized not in _NAKED_REMEDIATION_MODES:
        normalized = "off"
    for known in _NAKED_REMEDIATION_MODES:
        naked_position_remediation_mode_status.labels(mode=known).set(
            1 if known == normalized else 0
        )


# ============================================================
# OTel SDK instruments (dual-export — OTLP push to Grafana Alloy)
# ============================================================
# Per #415: registered ALONGSIDE the prometheus_client instruments above (the
# Prometheus pull path is unchanged). These flow through the MeterProvider wired
# by petrosa_otel.setup_telemetry(); get_meter() returns a proxy meter before the
# provider is installed, so module-level creation is safe. Names mirror the
# prometheus metric names for cross-system correlation in Grafana.
meter = get_meter("tradeengine.metrics")

# Order execution
otel_orders_executed_by_type = meter.create_counter(
    "tradeengine_orders_executed_by_type_total",
    description="Total orders executed by type (OTLP dual-export)",
)
otel_order_failures = meter.create_counter(
    "tradeengine_order_failures_total",
    description="Total order execution failures (OTLP dual-export)",
)
otel_order_execution_latency_seconds = meter.create_histogram(
    "tradeengine_order_execution_latency_seconds",
    description="Order execution latency in seconds (OTLP dual-export)",
    unit="s",
)

# Position / PnL
otel_positions_opened = meter.create_counter(
    "tradeengine_positions_opened_total",
    description="Total positions opened (OTLP dual-export)",
)
otel_positions_closed = meter.create_counter(
    "tradeengine_positions_closed_total",
    description="Total positions closed (OTLP dual-export)",
)
otel_position_pnl_usd = meter.create_histogram(
    "tradeengine_position_pnl_usd",
    description="Per-position realized PnL in USD (OTLP dual-export)",
)
otel_total_realized_pnl_usd = meter.create_up_down_counter(
    "tradeengine_total_realized_pnl_usd",
    description="Cumulative realized PnL in USD (OTLP dual-export)",
)
otel_total_unrealized_pnl_usd = meter.create_up_down_counter(
    "tradeengine_total_unrealized_pnl_usd",
    description="Total unrealized PnL in USD (OTLP dual-export)",
)
otel_total_daily_pnl_usd = meter.create_up_down_counter(
    "tradeengine_total_daily_pnl_usd",
    description="Total daily PnL in USD (OTLP dual-export)",
)

# Risk management
otel_risk_rejections = meter.create_counter(
    "tradeengine_risk_rejections_total",
    description="Total orders rejected by risk management (OTLP dual-export)",
)
otel_risk_checks = meter.create_counter(
    "tradeengine_risk_checks_total",
    description="Total risk checks performed (OTLP dual-export)",
)

# #448 — position persist failures (OTLP dual-export)
otel_position_persist_failed = meter.create_counter(
    "petrosa_tradeengine_position_persist_failed_total",
    description="Position write failures after retry exhaustion (OTLP dual-export)",
)

# #451 — DataManager boot probe (dual-export)
dm_boot_probe_total = Counter(
    "tradeengine_dm_boot_probe_total",
    "Boot-time DataManager write-then-read probe results (success or failure)",
    ["result"],
)

otel_dm_boot_probe_total = meter.create_counter(
    "tradeengine_dm_boot_probe_total",
    description="Boot-time DataManager write-then-read probe results (OTLP dual-export)",
)

# #497 — OCO orphan metrics (OTLP dual-export so Grafana Cloud alert fires)
# oco_orphan_leg_total: partial OCO failure events by surviving-leg cancel outcome.
# Mirrors the prometheus_client counter above; .add() is called alongside .inc()
# at the two sites in dispatcher.py.
otel_oco_orphan_leg = meter.create_counter(
    "petrosa_tradeengine_oco_orphan_leg_total",
    description="OCO partial-failure events split by surviving-leg cancel outcome (OTLP dual-export)",
)

# oco_orphan_count: current gauge of unhedged positions (the pre-existing
# `tradeengine-oco-pair-orphan` alert fires on this). Uses an UpDownCounter
# (OTel equivalent of a Gauge) so it can decrement when orphans are resolved.
otel_oco_orphan_count = meter.create_up_down_counter(
    "petrosa_tradeengine_oco_orphan_count",
    description="Current number of OCO orphans: positions lacking their full stop/target pair (OTLP dual-export)",
)

# #972 — OCO pair age (OTLP dual-export). Synchronous gauge; .set() is called
# alongside the prometheus_client Gauge each _monitor_orders cycle. Mirrors the
# prometheus metric name for cross-system correlation in Grafana.
otel_oco_pair_age_seconds = meter.create_gauge(
    "petrosa_tradeengine_oco_pair_age_seconds",
    description="Age in seconds of each active OCO pair (time since it entered active_oco_pairs) (OTLP dual-export)",
    unit="s",
)
