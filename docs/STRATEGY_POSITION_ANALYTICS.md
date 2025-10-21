# Strategy Position Analytics - SQL Queries

This document provides SQL queries for analyzing strategy position performance, contributions, and profit attribution.

## Table of Contents
1. [Strategy Performance](#strategy-performance)
2. [TP vs SL Analysis](#tp-vs-sl-analysis)
3. [Position Contributions](#position-contributions)
4. [Exchange Position Tracking](#exchange-position-tracking)
5. [Advanced Analytics](#advanced-analytics)

## Strategy Performance

### 1. Overall Strategy Statistics

```sql
-- Get comprehensive statistics for each strategy
SELECT
    strategy_id,
    COUNT(*) as total_positions,
    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_positions,
    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_positions,
    SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) as tp_hits,
    SUM(CASE WHEN close_reason = 'stop_loss' THEN 1 ELSE 0 END) as sl_hits,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
    ROUND(AVG(realized_pnl), 2) as avg_pnl_usd,
    ROUND(SUM(realized_pnl), 2) as total_pnl_usd,
    ROUND(AVG(realized_pnl_pct), 2) as avg_pnl_pct,
    ROUND(AVG(TIMESTAMPDIFF(SECOND, entry_time, exit_time)) / 60, 2) as avg_duration_minutes
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id
ORDER BY total_pnl_usd DESC;
```

### 2. Strategy Win Rate

```sql
-- Calculate win rate and profit factor for each strategy
SELECT
    strategy_id,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
    ROUND(
        (SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)) * 100,
        2
    ) as win_rate_pct,
    ROUND(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 2) as total_profit,
    ROUND(ABS(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)), 2) as total_loss,
    ROUND(
        SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) /
        ABS(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)),
        2
    ) as profit_factor
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id
ORDER BY profit_factor DESC;
```

## TP vs SL Analysis

### 3. TP and SL Hit Rates

```sql
-- Analyze how often each strategy hits TP vs SL
SELECT
    strategy_id,
    symbol,
    side,
    COUNT(*) as total_closes,
    SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) as tp_count,
    SUM(CASE WHEN close_reason = 'stop_loss' THEN 1 ELSE 0 END) as sl_count,
    SUM(CASE WHEN close_reason = 'manual' THEN 1 ELSE 0 END) as manual_count,
    ROUND(
        (SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) / COUNT(*)) * 100,
        2
    ) as tp_hit_rate_pct,
    ROUND(
        (SUM(CASE WHEN close_reason = 'stop_loss' THEN 1 ELSE 0 END) / COUNT(*)) * 100,
        2
    ) as sl_hit_rate_pct,
    ROUND(AVG(CASE WHEN close_reason = 'take_profit' THEN realized_pnl END), 2) as avg_tp_pnl,
    ROUND(AVG(CASE WHEN close_reason = 'stop_loss' THEN realized_pnl END), 2) as avg_sl_pnl
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id, symbol, side
ORDER BY strategy_id, symbol;
```

### 4. TP vs SL Performance Comparison

```sql
-- Compare TP and SL performance for a specific strategy
SELECT
    close_reason,
    COUNT(*) as count,
    ROUND(AVG(realized_pnl), 2) as avg_pnl_usd,
    ROUND(SUM(realized_pnl), 2) as total_pnl_usd,
    ROUND(AVG(realized_pnl_pct), 2) as avg_pnl_pct,
    ROUND(AVG(TIMESTAMPDIFF(SECOND, entry_time, exit_time)) / 60, 2) as avg_duration_min
FROM strategy_positions
WHERE status = 'closed'
  AND strategy_id = 'momentum_v1'  -- Replace with your strategy
GROUP BY close_reason
ORDER BY total_pnl_usd DESC;
```

## Position Contributions

### 5. Contribution Summary by Strategy

```sql
-- See which strategies contributed to which positions and their PnL
SELECT
    pc.strategy_id,
    pc.symbol,
    pc.position_side,
    COUNT(*) as total_contributions,
    ROUND(SUM(pc.contribution_quantity), 6) as total_quantity_contributed,
    ROUND(AVG(pc.contribution_entry_price), 2) as avg_entry_price,
    ROUND(SUM(pc.contribution_pnl), 2) as total_pnl_from_contributions,
    ROUND(AVG(pc.contribution_pnl_pct), 2) as avg_contribution_pnl_pct
FROM position_contributions pc
WHERE pc.status = 'closed'
GROUP BY pc.strategy_id, pc.symbol, pc.position_side
ORDER BY total_pnl_from_contributions DESC;
```

### 6. Multi-Strategy Position Analysis

```sql
-- Find positions that multiple strategies contributed to
SELECT
    pc.exchange_position_key,
    pc.symbol,
    pc.position_side,
    COUNT(DISTINCT pc.strategy_id) as num_contributing_strategies,
    GROUP_CONCAT(DISTINCT pc.strategy_id ORDER BY pc.contribution_time) as strategies,
    ROUND(SUM(pc.contribution_quantity), 6) as total_quantity,
    ROUND(SUM(pc.contribution_pnl), 2) as total_pnl,
    COUNT(*) as total_contributions
FROM position_contributions pc
WHERE pc.status = 'closed'
GROUP BY pc.exchange_position_key, pc.symbol, pc.position_side
HAVING num_contributing_strategies > 1
ORDER BY total_pnl DESC;
```

### 7. Contribution Timeline

```sql
-- See the sequence of contributions to a specific position
SELECT
    pc.position_sequence,
    pc.strategy_id,
    pc.contribution_quantity,
    pc.contribution_entry_price,
    pc.contribution_time,
    pc.exchange_quantity_before,
    pc.exchange_quantity_after,
    pc.contribution_pnl,
    pc.close_reason
FROM position_contributions pc
WHERE pc.exchange_position_key = 'BTCUSDT_LONG'  -- Replace with your position
ORDER BY pc.position_sequence;
```

## Exchange Position Tracking

### 8. Exchange Position Summary

```sql
-- Current state of all exchange positions
SELECT
    ep.symbol,
    ep.side,
    ep.current_quantity,
    ep.weighted_avg_price,
    ep.status,
    ep.total_contributions,
    ep.contributing_strategies,
    ROUND(TIMESTAMPDIFF(MINUTE, ep.first_entry_time, ep.last_update_time), 2) as duration_minutes
FROM exchange_positions ep
ORDER BY ep.last_update_time DESC;
```

### 9. Open vs Closed Exchange Positions

```sql
-- Summary of open and closed exchange positions
SELECT
    symbol,
    side,
    status,
    COUNT(*) as count,
    ROUND(AVG(current_quantity), 6) as avg_quantity,
    ROUND(AVG(weighted_avg_price), 2) as avg_price,
    AVG(total_contributions) as avg_contributions_per_position
FROM exchange_positions
GROUP BY symbol, side, status
ORDER BY symbol, side, status;
```

## Advanced Analytics

### 10. Strategy Profitability Over Time

```sql
-- Daily PnL by strategy
SELECT
    DATE(exit_time) as trade_date,
    strategy_id,
    COUNT(*) as trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(SUM(realized_pnl), 2) as daily_pnl_usd,
    ROUND(AVG(realized_pnl_pct), 2) as avg_return_pct
FROM strategy_positions
WHERE status = 'closed'
  AND exit_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)  -- Last 30 days
GROUP BY DATE(exit_time), strategy_id
ORDER BY trade_date DESC, daily_pnl_usd DESC;
```

### 11. Best and Worst Trades by Strategy

```sql
-- Top 10 best and worst trades for each strategy
(SELECT
    'BEST' as trade_type,
    strategy_id,
    symbol,
    side,
    entry_price,
    exit_price,
    realized_pnl,
    realized_pnl_pct,
    close_reason,
    exit_time
FROM strategy_positions
WHERE status = 'closed'
ORDER BY realized_pnl DESC
LIMIT 10)

UNION ALL

(SELECT
    'WORST' as trade_type,
    strategy_id,
    symbol,
    side,
    entry_price,
    exit_price,
    realized_pnl,
    realized_pnl_pct,
    close_reason,
    exit_time
FROM strategy_positions
WHERE status = 'closed'
ORDER BY realized_pnl ASC
LIMIT 10)

ORDER BY trade_type, realized_pnl DESC;
```

### 12. Strategy Comparison Matrix

```sql
-- Compare all strategies side by side
SELECT
    strategy_id,
    COUNT(*) as total_trades,
    ROUND((SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)) * 100, 1) as win_rate,
    ROUND(SUM(realized_pnl), 2) as total_pnl,
    ROUND(AVG(realized_pnl), 2) as avg_pnl_per_trade,
    ROUND(MAX(realized_pnl), 2) as best_trade,
    ROUND(MIN(realized_pnl), 2) as worst_trade,
    ROUND(
        (SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) / COUNT(*)) * 100,
        1
    ) as tp_hit_rate,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, entry_time, exit_time)), 0) as avg_duration_min
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id
ORDER BY total_pnl DESC;
```

### 13. Strategy Performance by Symbol

```sql
-- How does each strategy perform on different symbols?
SELECT
    strategy_id,
    symbol,
    side,
    COUNT(*) as trades,
    ROUND((SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)) * 100, 1) as win_rate,
    ROUND(SUM(realized_pnl), 2) as total_pnl,
    ROUND(AVG(realized_pnl_pct), 2) as avg_pnl_pct
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id, symbol, side
HAVING trades >= 5  -- At least 5 trades for statistical relevance
ORDER BY strategy_id, total_pnl DESC;
```

### 14. Contribution Attribution Report

```sql
-- Detailed attribution of PnL to contributing strategies
SELECT
    ep.exchange_position_key,
    ep.symbol,
    ep.side,
    ep.status,
    COUNT(pc.contribution_id) as total_contributions,
    GROUP_CONCAT(
        CONCAT(pc.strategy_id, ': $', ROUND(pc.contribution_pnl, 2))
        ORDER BY pc.position_sequence
        SEPARATOR ' | '
    ) as strategy_contributions,
    ROUND(SUM(pc.contribution_pnl), 2) as total_attributed_pnl,
    ROUND(ep.current_quantity, 6) as remaining_quantity,
    ep.weighted_avg_price
FROM exchange_positions ep
LEFT JOIN position_contributions pc ON ep.exchange_position_key = pc.exchange_position_key
WHERE pc.status = 'closed'
GROUP BY ep.exchange_position_key, ep.symbol, ep.side, ep.status
ORDER BY total_attributed_pnl DESC;
```

### 15. Strategy Correlation Analysis

```sql
-- See if strategies tend to enter positions on the same symbols at the same time
SELECT
    pc1.symbol,
    pc1.strategy_id as strategy_1,
    pc2.strategy_id as strategy_2,
    COUNT(*) as simultaneous_entries,
    ROUND(AVG(ABS(TIMESTAMPDIFF(SECOND, pc1.contribution_time, pc2.contribution_time))), 0) as avg_time_diff_seconds
FROM position_contributions pc1
JOIN position_contributions pc2
    ON pc1.exchange_position_key = pc2.exchange_position_key
    AND pc1.symbol = pc2.symbol
    AND pc1.strategy_id < pc2.strategy_id  -- Avoid duplicates
WHERE ABS(TIMESTAMPDIFF(MINUTE, pc1.contribution_time, pc2.contribution_time)) <= 5  -- Within 5 minutes
GROUP BY pc1.symbol, pc1.strategy_id, pc2.strategy_id
HAVING simultaneous_entries >= 3
ORDER BY simultaneous_entries DESC;
```

## Usage Examples

### Python Example: Query Strategy Performance

```python
from shared.mysql_client import mysql_client

async def get_strategy_performance(strategy_id: str):
    """Get performance metrics for a strategy"""
    query = """
        SELECT
            COUNT(*) as total_positions,
            SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) as tp_hits,
            SUM(CASE WHEN close_reason = 'stop_loss' THEN 1 ELSE 0 END) as sl_hits,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl_pct), 2) as avg_pnl_pct
        FROM strategy_positions
        WHERE status = 'closed' AND strategy_id = %s
    """

    result = await mysql_client.execute_query(query, (strategy_id,))
    return result[0] if result else None

# Usage
performance = await get_strategy_performance("momentum_v1")
print(f"Strategy Performance: {performance}")
```

### Python Example: Get Multi-Strategy Positions

```python
async def get_multi_strategy_positions():
    """Find positions that multiple strategies contributed to"""
    query = """
        SELECT
            pc.exchange_position_key,
            pc.symbol,
            COUNT(DISTINCT pc.strategy_id) as num_strategies,
            GROUP_CONCAT(DISTINCT pc.strategy_id) as strategies,
            ROUND(SUM(pc.contribution_pnl), 2) as total_pnl
        FROM position_contributions pc
        WHERE pc.status = 'closed'
        GROUP BY pc.exchange_position_key, pc.symbol
        HAVING num_strategies > 1
        ORDER BY total_pnl DESC
    """

    results = await mysql_client.execute_query(query)
    return results
```

## Grafana Dashboard Queries

### PromQL for Strategy Position Metrics

```promql
# Total PnL by strategy (from application metrics)
sum by (strategy_id) (tradeengine_strategy_position_pnl_total)

# TP hit rate
sum by (strategy_id) (tradeengine_strategy_positions_closed_total{close_reason="take_profit"}) /
sum by (strategy_id) (tradeengine_strategy_positions_closed_total)

# Active strategy positions
tradeengine_strategy_positions_open_total
```

## Performance Considerations

1. **Indexes**: All tables have appropriate indexes for these queries
2. **Date Range**: Always use date filters for large datasets
3. **Aggregations**: Use `GROUP BY` wisely for performance
4. **JSON Fields**: Avoid heavy JSON processing in queries

## Best Practices

1. **Regular Analysis**: Run performance queries daily to track strategy health
2. **Thresholds**: Set alerts for strategies with low win rates or excessive SL hits
3. **Comparison**: Compare strategies on the same symbols for fair evaluation
4. **Time Windows**: Use rolling windows (7d, 30d, 90d) for trend analysis
5. **Attribution**: Review contribution attribution for position building strategies

## Troubleshooting

### Query is Slow
- Add date filters: `WHERE exit_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)`
- Use EXPLAIN to check query plan
- Ensure indexes exist on commonly filtered columns

### Missing Data
- Check that strategy_position_manager is initialized
- Verify MySQL connection is active
- Check that signals are creating strategy positions

### Incorrect PnL
- Verify entry_price and exit_price are correct
- Check position_side (LONG vs SHORT) for PnL calculation
- Ensure commission is included in total PnL

## References

- [Strategy Position Manager](../tradeengine/strategy_position_manager.py)
- [Database Schema](../scripts/create_strategy_positions_table.sql)
- [Hedge Mode Position Tracking](HEDGE_MODE_POSITION_TRACKING.md)
