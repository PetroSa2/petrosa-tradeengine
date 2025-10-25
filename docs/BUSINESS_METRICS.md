# Trade Execution Business Metrics

## Overview

This document describes the custom business metrics added to the tradeengine for monitoring order execution, risk management, and trading performance. These metrics provide real-time observability into critical trading operations.

## Metrics Categories

### 1. Order Execution Metrics

#### `tradeengine_orders_executed_by_type_total`
- **Type**: Counter
- **Description**: Total number of orders executed, broken down by order type, side, symbol, and exchange
- **Labels**:
  - `order_type`: Type of order (market, limit, stop, take_profit, etc.)
  - `side`: Order side (buy/sell)
  - `symbol`: Trading symbol (e.g., BTCUSDT)
  - `exchange`: Exchange identifier (binance)
- **Use Cases**:
  - Monitor order execution rate by type
  - Detect anomalies in order patterns
  - Calculate order distribution across symbols

**Example Query**:
```promql
# Order execution rate in the last 5 minutes
rate(tradeengine_orders_executed_by_type_total[5m])

# Total orders by type in the last hour
sum by (order_type) (increase(tradeengine_orders_executed_by_type_total[1h]))
```

#### `tradeengine_order_execution_latency_seconds`
- **Type**: Histogram
- **Description**: Time from signal receipt to order execution completion (in seconds)
- **Labels**:
  - `symbol`: Trading symbol
  - `order_type`: Type of order
  - `exchange`: Exchange identifier
- **Buckets**: [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0] seconds
- **Use Cases**:
  - Monitor end-to-end execution latency
  - Identify performance bottlenecks
  - Set SLA alerts for order execution time

**Example Query**:
```promql
# p95 latency across all symbols
histogram_quantile(0.95, sum(rate(tradeengine_order_execution_latency_seconds_bucket[5m])) by (le))

# p99 latency by symbol
histogram_quantile(0.99, sum(rate(tradeengine_order_execution_latency_seconds_bucket[5m])) by (le, symbol))
```

#### `tradeengine_order_failures_total`
- **Type**: Counter
- **Description**: Total number of order execution failures
- **Labels**:
  - `symbol`: Trading symbol
  - `order_type`: Type of order
  - `failure_reason`: Reason for failure (truncated to 50 chars)
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Track order failure rate
  - Identify common failure reasons
  - Alert on sudden spikes in failures

**Example Query**:
```promql
# Failure rate in the last 5 minutes
rate(tradeengine_order_failures_total[5m])

# Top 5 failure reasons in the last hour
topk(5, sum by (failure_reason) (increase(tradeengine_order_failures_total[1h])))
```

### 2. Risk Management Metrics

#### `tradeengine_risk_rejections_total`
- **Type**: Counter
- **Description**: Total number of orders rejected by risk management
- **Labels**:
  - `reason`: Rejection reason (position_limits_exceeded, daily_loss_limits_exceeded)
  - `symbol`: Trading symbol
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Monitor risk management effectiveness
  - Track rejection patterns by symbol
  - Alert on high rejection rates

**Example Query**:
```promql
# Rejection rate by reason
rate(tradeengine_risk_rejections_total[5m])

# Total rejections in the last hour
sum by (reason) (increase(tradeengine_risk_rejections_total[1h]))
```

#### `tradeengine_risk_checks_total`
- **Type**: Counter
- **Description**: Total number of risk checks performed
- **Labels**:
  - `check_type`: Type of check (position_limits, daily_loss_limits)
  - `result`: Check result (checking, passed, rejected)
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Calculate risk check success rate
  - Monitor risk management throughput
  - Verify risk checks are being performed

**Example Query**:
```promql
# Risk check success rate
sum(rate(tradeengine_risk_checks_total{result="passed"}[5m])) /
sum(rate(tradeengine_risk_checks_total[5m])) * 100
```

### 3. Position & PnL Metrics

#### `tradeengine_current_position_size`
- **Type**: Gauge
- **Description**: Current position size by symbol and side
- **Labels**:
  - `symbol`: Trading symbol
  - `position_side`: Position side (LONG/SHORT)
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Monitor current exposure by symbol
  - Visualize position distribution
  - Alert on large positions

**Example Query**:
```promql
# Current positions across all symbols
tradeengine_current_position_size

# Total exposure (sum of all positions)
sum(tradeengine_current_position_size)
```

#### `tradeengine_total_position_value_usd`
- **Type**: Gauge
- **Description**: Total value of all open positions in USD
- **Labels**:
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Monitor total capital deployed
  - Calculate portfolio utilization
  - Track exposure trends

**Example Query**:
```promql
# Total position value
tradeengine_total_position_value_usd

# Position value change over time
delta(tradeengine_total_position_value_usd[1h])
```

#### `tradeengine_total_realized_pnl_usd`
- **Type**: Gauge
- **Description**: Cumulative realized PnL in USD (aggregate across all positions)
- **Labels**:
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Track total profits/losses
  - Calculate overall win rate
  - Monitor cumulative trading performance

**Example Query**:
```promql
# Total realized PnL
tradeengine_total_realized_pnl_usd

# Realized PnL change over time
rate(tradeengine_total_realized_pnl_usd[5m])
```

#### `tradeengine_total_unrealized_pnl_usd`
- **Type**: Gauge
- **Description**: Total unrealized PnL in USD (aggregate across all open positions)
- **Labels**:
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Monitor total floating profits/losses
  - Track aggregate mark-to-market P&L
  - Alert on large unrealized losses

**Example Query**:
```promql
# Total unrealized PnL
tradeengine_total_unrealized_pnl_usd

# Unrealized PnL as % of total position value
(tradeengine_total_unrealized_pnl_usd / tradeengine_total_position_value_usd) * 100
```

#### `tradeengine_total_daily_pnl_usd`
- **Type**: Gauge
- **Description**: Total daily PnL in USD (resets at midnight UTC)
- **Labels**:
  - `exchange`: Exchange identifier
- **Use Cases**:
  - Track intraday performance
  - Calculate daily win rate
  - Monitor risk management effectiveness

**Example Query**:
```promql
# Current daily PnL
tradeengine_total_daily_pnl_usd

# Average daily PnL over the last 7 days
avg_over_time(tradeengine_total_daily_pnl_usd[7d])
```

## Grafana Dashboard

A comprehensive Grafana dashboard is available at `docs/grafana-trade-execution-dashboard.json`.

### Dashboard Sections

1. **Order Execution Rate**: Real-time order execution rate by type and symbol
2. **Order Execution Latency**: p50, p95, and p99 latency percentiles
3. **Risk Rejections**: Count of rejections by reason
4. **Risk Check Success Rate**: Percentage of passed risk checks
5. **Current Position Sizes**: Real-time position sizes by symbol
6. **PnL Monitoring**: Unrealized, realized, and daily PnL gauges
7. **Order Success vs Failures**: Pie chart of successful vs failed orders
8. **Order Failures by Reason**: Table of failures grouped by reason
9. **Order Execution Heatmap**: Hour-by-hour execution patterns

### Importing the Dashboard

```bash
# Using Grafana API
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d @docs/grafana-trade-execution-dashboard.json

# Or import via Grafana UI:
# 1. Go to Dashboards → Import
# 2. Upload docs/grafana-trade-execution-dashboard.json
# 3. Select Prometheus datasource
# 4. Click "Import"
```

## Alert Rules

### Recommended Alerts

#### High Order Failure Rate
```yaml
alert: HighOrderFailureRate
expr: |
  sum(rate(tradeengine_order_failures_total[5m])) /
  sum(rate(tradeengine_orders_executed_by_type_total[5m])) > 0.1
for: 5m
labels:
  severity: warning
annotations:
  summary: "Order failure rate above 10%"
  description: "{{$value | humanizePercentage}} of orders are failing"
```

#### High Execution Latency
```yaml
alert: HighExecutionLatency
expr: |
  histogram_quantile(0.95,
    sum(rate(tradeengine_order_execution_latency_seconds_bucket[5m])) by (le)
  ) > 10
for: 5m
labels:
  severity: warning
annotations:
  summary: "p95 execution latency above 10 seconds"
  description: "Execution latency is {{$value}}s"
```

#### High Risk Rejection Rate
```yaml
alert: HighRiskRejectionRate
expr: |
  sum(rate(tradeengine_risk_rejections_total[5m])) > 5
for: 5m
labels:
  severity: critical
annotations:
  summary: "High number of risk rejections"
  description: "{{$value}} rejections per second"
```

#### Large Daily Loss
```yaml
alert: LargeDailyLoss
expr: |
  tradeengine_daily_pnl_usd < -5000
for: 1m
labels:
  severity: critical
annotations:
  summary: "Daily PnL below -$5000"
  description: "Current daily PnL: ${{$value}}"
```

## Integration with Existing Systems

### OpenTelemetry

All metrics are automatically exported to Grafana Cloud via OTLP when `OTEL_ENABLED=true`. The OTLP exporter endpoint is configured in the `petrosa-common-config` ConfigMap.

### Prometheus

Metrics are exposed on the `/metrics` endpoint and scraped by Prometheus every 15 seconds (configurable).

**Scrape Configuration**:
```yaml
scrape_configs:
  - job_name: 'tradeengine'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - petrosa-apps
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: petrosa-tradeengine
        action: keep
      - source_labels: [__meta_kubernetes_pod_container_port_name]
        regex: metrics
        action: keep
```

## Testing

Comprehensive tests for all business metrics are available in `tests/test_business_metrics.py`.

**Run Tests**:
```bash
# Run all metric tests
pytest tests/test_business_metrics.py -v

# Run specific test class
pytest tests/test_business_metrics.py::TestOrderExecutionMetrics -v

# Run with coverage
pytest tests/test_business_metrics.py --cov=tradeengine.metrics --cov-report=html
```

## Best Practices

### Metric Naming
- All metrics use the `tradeengine_` prefix
- Business metrics use descriptive names (e.g., `order_execution_latency_seconds`)
- Metrics follow Prometheus naming conventions

### Label Cardinality
- Keep label cardinality low to avoid performance issues
- Symbol labels are limited to actively traded symbols
- Failure reasons are truncated to 50 characters

### Retention
- Metrics are retained according to Prometheus/Grafana Cloud retention policies
- Consider aggregating historical data for long-term analysis

### Performance Impact
- Metrics emission adds minimal overhead (<1ms per operation)
- Gauges are updated in-memory and synced periodically
- Histograms use pre-defined buckets for efficient storage

## Troubleshooting

### Metrics Not Appearing
1. Check that Prometheus is scraping the `/metrics` endpoint
2. Verify OTEL is enabled: `kubectl get configmap petrosa-common-config`
3. Check pod logs for metric emission: `kubectl logs -l app=petrosa-tradeengine`

### Incorrect Values
1. Verify position manager is updating correctly
2. Check for race conditions in distributed locks
3. Review risk management check logic

### High Cardinality
1. Limit the number of symbols being traded
2. Truncate failure reasons to reasonable length
3. Use label matchers in queries to reduce cardinality

## Future Enhancements

Planned improvements to business metrics:

1. **Order Book Depth Metrics**: Track order book liquidity and spread
2. **Slippage Metrics**: Measure execution price vs expected price
3. **Commission Tracking**: Monitor trading fees and costs
4. **Strategy Performance**: Per-strategy success rates and PnL
5. **Market Impact**: Measure price impact of large orders

## References

- [Prometheus Best Practices](https://prometheus.io/docs/practices/naming/)
- [Grafana Dashboard Documentation](https://grafana.com/docs/grafana/latest/dashboards/)
- [OpenTelemetry Metrics](https://opentelemetry.io/docs/concepts/signals/metrics/)
- [petrosa_k8s Master Cursorrules](/Users/yurisa2/petrosa/petrosa_k8s/.cursorrules)
