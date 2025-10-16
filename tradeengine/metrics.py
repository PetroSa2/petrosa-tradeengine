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
