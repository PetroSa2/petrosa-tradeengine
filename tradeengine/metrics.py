"""
Position Tracking Metrics - Prometheus metrics for hedge mode position tracking

⚠️ CRITICAL: These metrics follow the exact same pattern as existing metrics in
dispatcher.py, api.py, and consumer.py. DO NOT modify the pattern or it will
break the observability stack.

All metrics are automatically exported to Grafana Cloud via OTLP.
"""

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
