# Hedge Mode Position Tracking Implementation

## Overview

This document describes the hedge mode position tracking implementation for the Petrosa Trading Engine. The system now supports Binance Futures hedge mode, allowing simultaneous LONG and SHORT positions on the same symbol with comprehensive position lifecycle tracking.

## Key Features

1. **Hedge Mode Support**: Hold both LONG and SHORT positions simultaneously
2. **Dual Persistence**: Positions tracked in both MongoDB (real-time) and MySQL (analytics)
3. **Comprehensive Metrics**: Full observability with Prometheus metrics exported to Grafana Cloud
4. **Position Lifecycle Tracking**: Complete tracking from entry to exit with PnL calculation
5. **Multi-Exchange Ready**: Architecture supports multiple exchanges (currently Binance)

## Architecture

### Position Tracking Flow

```
Signal → Dispatcher → Order (with position_id) → Binance Execution
                                                         ↓
                                                 Create Position Record
                                                         ↓
                                        ┌────────────────┴────────────────┐
                                        ↓                                 ↓
                                    MongoDB                            MySQL
                                (Real-time State)              (Analytics Ready)
                                        ↓                                 ↓
                                        └────────────────┬────────────────┘
                                                         ↓
                                                  Metrics Export
                                                         ↓
                                                  Grafana Cloud
```

### Components

#### 1. Position ID Generation
- **Location**: `tradeengine/dispatcher.py`
- **Method**: `_signal_to_order()`
- **Implementation**: UUID4 for globally unique position IDs
- **Position Side**: Automatically determined (buy=LONG, sell=SHORT)

#### 2. Binance Hedge Mode Support
- **Location**: `tradeengine/exchange/binance.py`
- **Changes**: Added `positionSide` parameter to all order types
- **Verification**: `verify_hedge_mode()` method checks account configuration

#### 3. Position Manager
- **Location**: `tradeengine/position_manager.py`
- **Key Methods**:
  - `create_position_record()`: Creates position on order execution
  - `close_position_record()`: Updates position on closure
  - `_export_position_opened_metrics()`: Exports metrics on open
  - `_export_position_closed_metrics()`: Exports metrics on close

#### 4. MySQL Client
- **Location**: `shared/mysql_client.py`
- **Methods**:
  - `create_position()`: Insert new position
  - `update_position()`: Update existing position
  - `get_position()`: Retrieve position by ID
  - `get_open_positions()`: Get all open positions

#### 5. Metrics
- **Location**: `tradeengine/metrics.py`
- **Metrics Exported**:
  - `tradeengine_positions_opened_total`: Position open counter
  - `tradeengine_positions_closed_total`: Position close counter
  - `tradeengine_position_pnl_usd`: PnL in USD (histogram)
  - `tradeengine_position_pnl_percentage`: PnL as percentage (histogram)
  - `tradeengine_position_duration_seconds`: Position duration (histogram)
  - `tradeengine_position_roi`: Return on investment (histogram)
  - `tradeengine_positions_winning_total`: Winning positions counter
  - `tradeengine_positions_losing_total`: Losing positions counter
  - `tradeengine_position_commission_usd`: Commission costs (histogram)

## Database Schemas

### MongoDB Schema

```javascript
{
    position_id: "uuid",
    strategy_id: "strategy_name",
    exchange: "binance",
    symbol: "BTCUSDT",
    position_side: "LONG|SHORT",
    entry_price: 45000.0,
    quantity: 0.001,
    entry_time: ISODate("2025-10-16T..."),
    stop_loss: 43000.0,
    take_profit: 47000.0,
    status: "open|closed",
    metadata: {...},
    // Exchange data
    exchange_position_id: "123456",
    entry_order_id: "order-123",
    entry_trade_ids: ["trade-1", "trade-2"],
    stop_loss_order_id: "sl-order-123",
    take_profit_order_id: "tp-order-123",
    commission_asset: "USDT",
    commission_total: 0.045,
    // Closure data (populated on close)
    exit_price: 47000.0,
    exit_time: ISODate("2025-10-16T..."),
    exit_order_id: "exit-order-123",
    exit_trade_ids: ["exit-trade-1"],
    pnl: 2.0,
    pnl_pct: 4.44,
    pnl_after_fees: 1.955,
    duration_seconds: 3600,
    close_reason: "take_profit|stop_loss|manual",
    final_commission: 0.047
}
```

### MySQL Schema

```sql
CREATE TABLE positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    position_id VARCHAR(255) UNIQUE NOT NULL,
    strategy_id VARCHAR(255) NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    position_side ENUM('LONG', 'SHORT') NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    entry_time DATETIME NOT NULL,
    stop_loss DECIMAL(20, 8),
    take_profit DECIMAL(20, 8),
    status ENUM('open', 'closed') NOT NULL,
    metadata JSON,
    -- Exchange-specific data
    exchange_position_id VARCHAR(255),
    entry_order_id VARCHAR(255),
    entry_trade_ids JSON,
    -- Closure data
    exit_price DECIMAL(20, 8),
    exit_time DATETIME,
    pnl DECIMAL(20, 8),
    pnl_pct DECIMAL(10, 4),
    pnl_after_fees DECIMAL(20, 8),
    duration_seconds INT,
    close_reason VARCHAR(50),
    -- Indexes for fast queries
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_exchange (exchange),
    INDEX idx_symbol (symbol),
    INDEX idx_status (status),
    INDEX idx_entry_time (entry_time)
);
```

## Setup Instructions

### 1. Database Setup

#### MySQL
```bash
# Run the schema creation script
mysql -u root -p < scripts/create_positions_table.sql
```

#### MongoDB
No setup required - positions collection created automatically.

### 2. Environment Configuration

Add to `.env` or Kubernetes ConfigMap:

```bash
# MySQL Configuration
MYSQL_URI=mysql+pymysql://user:pass@host:3306/database

# Hedge Mode
HEDGE_MODE_ENABLED=true
POSITION_MODE=hedge
```

### 3. Verify Hedge Mode

⚠️ **CRITICAL**: Before deploying, manually verify hedge mode is enabled on Binance:

```bash
# Run verification script
python scripts/verify_hedge_mode.py
```

**To Enable Hedge Mode on Binance**:
1. Log in to Binance Futures
2. Go to Settings (⚙️) → Preferences
3. Under "Position Mode", select "Hedge Mode"
4. Confirm (note: you cannot have open positions/orders when switching)

### 4. Deploy

```bash
# Deploy to Kubernetes
make deploy

# Check deployment
make k8s-status
```

## Usage

### Position Tracking is Automatic

Once deployed, position tracking is automatic:

1. **Signal Received** → Position ID generated
2. **Order Executed** → Position record created (MongoDB + MySQL)
3. **Position Opened** → Metrics exported
4. **Position Closed** → Record updated, final metrics exported

### Querying Positions

#### MongoDB
```javascript
// Get open positions
db.positions.find({ status: "open" })

// Get positions by strategy
db.positions.find({ strategy_id: "momentum_v1" })

// Get winning positions
db.positions.find({ pnl_after_fees: { $gt: 0 }, status: "closed" })
```

#### MySQL
```sql
-- Get open positions
SELECT * FROM positions WHERE status = 'open';

-- Get position performance by strategy
SELECT
    strategy_id,
    COUNT(*) as total_positions,
    SUM(CASE WHEN pnl_after_fees > 0 THEN 1 ELSE 0 END) as wins,
    AVG(pnl_after_fees) as avg_pnl,
    AVG(duration_seconds) as avg_duration
FROM positions
WHERE status = 'closed'
GROUP BY strategy_id;

-- Get top performing strategies
SELECT
    strategy_id,
    SUM(pnl_after_fees) as total_pnl
FROM positions
WHERE status = 'closed'
GROUP BY strategy_id
ORDER BY total_pnl DESC
LIMIT 10;
```

## Monitoring

### Grafana Dashboards

All metrics are automatically exported to Grafana Cloud. Create dashboards with:

**Position Performance Panel**:
- Metric: `tradeengine_position_pnl_usd`
- Aggregation: Sum by `strategy_id`
- Visualization: Time series

**Win Rate Panel**:
- Metrics: `tradeengine_positions_winning_total`, `tradeengine_positions_losing_total`
- Formula: `winning / (winning + losing)`
- Visualization: Stat

**Average Duration Panel**:
- Metric: `tradeengine_position_duration_seconds`
- Aggregation: Average by `strategy_id`
- Visualization: Bar chart

**Commission Costs Panel**:
- Metric: `tradeengine_position_commission_usd`
- Aggregation: Sum over time
- Visualization: Time series

### Prometheus Queries

```promql
# Total PnL by strategy
sum by (strategy_id) (tradeengine_position_pnl_usd)

# Win rate by strategy
sum by (strategy_id) (tradeengine_positions_winning_total) /
(sum by (strategy_id) (tradeengine_positions_winning_total) +
 sum by (strategy_id) (tradeengine_positions_losing_total))

# Average position duration
avg by (strategy_id) (tradeengine_position_duration_seconds)

# Total commissions
sum(tradeengine_position_commission_usd)
```

## Testing

### Unit Tests
```bash
# Run position tracking tests
pytest tests/test_position_tracking.py -v

# Run with coverage
pytest tests/test_position_tracking.py -v --cov=tradeengine
```

### Integration Testing
```bash
# Test hedge mode verification
python scripts/verify_hedge_mode.py

# Test end-to-end position lifecycle
# (requires live Binance testnet connection)
python examples/test_hedge_mode_positions.py
```

## Troubleshooting

### Position Not Being Created
- Check logs for errors in `create_position_record()`
- Verify MySQL connection: `make k8s-logs | grep "MySQL"`
- Ensure order has `position_id` field

### Metrics Not Appearing in Grafana
- Verify metrics endpoint: `curl http://localhost:8000/metrics | grep position`
- Check OTLP exporter configuration
- Ensure Prometheus is scraping the `/metrics` endpoint
- Check Grafana Cloud connection in Kubernetes

### Hedge Mode Not Working
- Run `python scripts/verify_hedge_mode.py`
- Check Binance account position mode setting
- Ensure no open positions when switching modes
- Verify `positionSide` parameter is included in order API calls

### MySQL Connection Issues
- Check `MYSQL_URI` environment variable
- Verify MySQL service is running
- Test connection: `mysql -h HOST -u USER -p`
- Check Kubernetes secret `petrosa-sensitive-credentials`

## Performance Considerations

1. **Database Writes**: Position records written on every order execution
2. **Metrics Cardinality**: Limited labels to prevent high cardinality
3. **MongoDB Indexing**: Automatic indexes on frequently queried fields
4. **MySQL Queries**: Optimized with composite indexes

## Security

1. **MySQL Credentials**: Stored in Kubernetes secrets
2. **MongoDB Credentials**: Stored in Kubernetes secrets
3. **API Keys**: Never logged or stored in position metadata
4. **PII Protection**: No personal information in position records

## Future Enhancements

1. **Position Analytics Dashboard**: Pre-built Grafana dashboard
2. **Real-time Position Monitoring**: WebSocket updates for open positions
3. **Advanced PnL Tracking**: Mark-to-market, unrealized PnL updates
4. **Strategy Backtesting**: Historical position replay
5. **Risk Alerts**: Automated alerts for poor-performing strategies
6. **Position Grouping**: Group related positions for portfolio analysis

## References

- [Binance Hedge Mode Documentation](https://www.binance.com/en/support/faq/hedge-mode)
- [Position Manager Source](../tradeengine/position_manager.py)
- [Metrics Definition](../tradeengine/metrics.py)
- [MySQL Schema](../scripts/create_positions_table.sql)
