# Production Validation Guide: Business Metrics

This document provides a comprehensive guide for validating business metrics in the production environment.

## Overview

Business metrics were implemented in PR #166 and tested locally. This validation ensures they work correctly in production.

**11 Business Metrics to Validate:**
1. `tradeengine_orders_executed_by_type_total` (Counter)
2. `tradeengine_order_execution_latency_seconds` (Histogram)
3. `tradeengine_order_failures_total` (Counter)
4. `tradeengine_risk_rejections_total` (Counter)
5. `tradeengine_risk_checks_total` (Counter)
6. `tradeengine_current_position_size` (Gauge)
7. `tradeengine_total_position_value_usd` (Gauge)
8. `tradeengine_total_realized_pnl_usd` (Gauge)
9. `tradeengine_total_unrealized_pnl_usd` (Gauge)
10. `tradeengine_total_daily_pnl_usd` (Gauge)
11. `tradeengine_order_success_rate` (Gauge)

## Validation Phases

### Phase 1: Endpoint Validation (30 min)

**Objective**: Verify metrics endpoint exposes all business metrics.

#### Step 1: Check Metrics Endpoint

```bash
# From within cluster
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep -E "tradeengine_(orders_executed_by_type|order_execution_latency|order_failures|risk_rejections|risk_checks|current_position_size|total_position_value|total_realized_pnl|total_unrealized_pnl|total_daily_pnl|order_success_rate)"

# Or use validation script
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  python scripts/validate_business_metrics.py --endpoint http://localhost:9090/metrics
```

**Expected Output:**
- HTTP 200 response
- All 11 metric names present
- No NaN or Inf values
- Response time < 500ms

#### Step 2: Verify Metric Types

```bash
# Check for Counter metrics (should have _total suffix)
curl -s http://tradeengine:9090/metrics | grep "tradeengine_orders_executed_by_type_total"

# Check for Histogram metrics (should have _bucket, _count, _sum)
curl -s http://tradeengine:9090/metrics | grep "tradeengine_order_execution_latency_seconds"

# Check for Gauge metrics (direct values)
curl -s http://tradeengine:9090/metrics | grep "tradeengine_current_position_size"
```

### Phase 2: Prometheus Integration (20 min)

**Objective**: Verify Prometheus is scraping and storing metrics.

#### Step 1: Check Prometheus Scrape Target

```bash
# Query Prometheus for tradeengine target status
# In Prometheus UI: http://prometheus:9090/targets
# Or via API:
curl http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job == "tradeengine")'
```

**Expected:**
- Target status: `up`
- Last scrape: < 60 seconds ago
- Scrape duration: < 500ms
- No scrape errors

#### Step 2: Verify Metrics in Prometheus

Run these queries in Prometheus expression browser:

```promql
# 1. Verify tradeengine is up and being scraped
up{job="tradeengine"} == 1

# 2. Check orders are being counted
rate(tradeengine_orders_executed_by_type_total[5m])

# 3. Verify latency tracking (should see values < 10s typically)
histogram_quantile(0.99, rate(tradeengine_order_execution_latency_seconds_bucket[5m]))

# 4. Confirm risk checks are running
rate(tradeengine_risk_checks_total[5m]) > 0

# 5. Validate position metrics exist
tradeengine_current_position_size{symbol="BTCUSDT"}

# 6. Check PnL metrics are non-zero (if trading active)
tradeengine_total_daily_pnl_usd != 0

# 7. Verify no order failures
rate(tradeengine_order_failures_total[5m])
```

**Expected Results:**
- All queries return data (not empty)
- Values are reasonable (latency < 10s, counters increasing)
- No NaN or Inf values

### Phase 3: Data Accuracy (20 min)

**Objective**: Verify metric values match actual trading activity.

#### Step 1: Compare Order Counts

```bash
# Get order count from MongoDB
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  python -c "
from pymongo import MongoClient
import os
client = MongoClient(os.environ['MONGODB_URI'])
db = client.petrosa
orders = db.trades.count_documents({})
print(f'MongoDB orders: {orders}')
"

# Compare with Prometheus metric
# Query: tradeengine_orders_executed_by_type_total
# Should match within ±1%
```

#### Step 2: Verify Latency Values

```promql
# Check latency percentiles
histogram_quantile(0.50, rate(tradeengine_order_execution_latency_seconds_bucket[5m]))  # p50
histogram_quantile(0.95, rate(tradeengine_order_execution_latency_seconds_bucket[5m]))  # p95
histogram_quantile(0.99, rate(tradeengine_order_execution_latency_seconds_bucket[5m]))  # p99
```

**Expected:**
- p50 < 2s
- p95 < 5s
- p99 < 10s

#### Step 3: Cross-Reference PnL

```bash
# Get PnL from accounting system (if available)
# Compare with:
# - tradeengine_total_realized_pnl_usd
# - tradeengine_total_unrealized_pnl_usd
# - tradeengine_total_daily_pnl_usd
```

### Phase 4: Performance Check (10 min)

**Objective**: Ensure metrics don't impact performance.

#### Step 1: Monitor CPU Usage

```bash
# Before metrics (baseline)
kubectl --kubeconfig=k8s/kubeconfig.yaml top pod -n petrosa-apps -l app=tradeengine

# During active trading (with metrics)
# Should see < 1% CPU increase
```

#### Step 2: Monitor Memory Usage

```bash
# Check memory footprint
kubectl --kubeconfig=k8s/kubeconfig.yaml top pod -n petrosa-apps -l app=tradeengine

# Metrics should add < 50MB memory
```

#### Step 3: Check Scrape Duration

```promql
# In Prometheus, check scrape_duration_seconds for tradeengine target
scrape_duration_seconds{job="tradeengine"}
```

**Expected:** < 500ms

#### Step 4: Review Logs

```bash
# Check for metric-related errors
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine --tail=1000 | \
  grep -iE "metric|prometheus|error" | grep -v "DEBUG"
```

**Expected:** No errors related to metrics

### Phase 5: 24-Hour Observation

**Objective**: Monitor metrics stability over full trading day.

#### Monitoring Checklist

- [ ] Metrics stable across full trading day
- [ ] No unexpected spikes or drops
- [ ] Counters monotonically increasing
- [ ] Gauges tracking state changes correctly
- [ ] No metric-related errors in logs
- [ ] Performance overhead remains < 1% CPU, < 50MB memory

#### Automated Monitoring Queries

Set up alerts for:

```promql
# Alert if metrics stop updating
up{job="tradeengine"} == 0

# Alert if latency exceeds threshold
histogram_quantile(0.99, rate(tradeengine_order_execution_latency_seconds_bucket[5m])) > 10

# Alert if order failures spike
rate(tradeengine_order_failures_total[5m]) > 0.1
```

## Validation Script

Use the automated validation script:

```bash
# From within cluster
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  python scripts/validate_business_metrics.py --endpoint http://localhost:9090/metrics --verbose
```

**Script validates:**
- ✅ All 11 metrics present
- ✅ No NaN or Inf values
- ✅ Counters not negative
- ✅ Histogram buckets valid
- ✅ Response time acceptable

## Troubleshooting

### Metrics Not Appearing

1. **Check endpoint is accessible:**
   ```bash
   curl http://tradeengine:9090/metrics
   ```

2. **Check Prometheus scrape config:**
   ```bash
   # Verify scrape target exists in Prometheus config
   kubectl --kubeconfig=k8s/kubeconfig.yaml get configmap prometheus-config -n monitoring -o yaml
   ```

3. **Check tradeengine logs:**
   ```bash
   kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine | grep -i metric
   ```

### Metrics Have NaN/Inf Values

1. **Check for division by zero in metric calculations**
2. **Verify position data is valid before metric emission**
3. **Check logs for calculation errors**

### Performance Issues

1. **Reduce metric cardinality** (fewer label combinations)
2. **Check scrape interval** (should be 15-30s, not too frequent)
3. **Monitor memory usage** (histograms can use more memory)

## Success Criteria

✅ **All validation phases pass:**
- Endpoint returns all 11 metrics
- Prometheus scraping successfully
- Metric values match expected behavior
- Performance impact < 1% CPU, < 50MB memory
- 24-hour observation period completed without issues

✅ **Documentation updated:**
- Validation results recorded
- Baseline values established
- Alert thresholds configured

## Related Documentation

- [Business Metrics Implementation](../docs/BUSINESS_METRICS.md)
- [Prometheus Querying Guide](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Histogram Best Practices](https://prometheus.io/docs/practices/histograms/)
