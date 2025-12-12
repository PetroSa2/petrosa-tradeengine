# Trade Engine Business Metrics Alerting Setup

## Overview

This document describes the alerting configuration for tradeengine business metrics. Alerts are configured using PrometheusRule CRDs and are evaluated by Prometheus/Alertmanager.

## Alert Configuration Location

**Kubernetes**: `petrosa_k8s/k8s/monitoring/prometheus-rules/tradeengine-business-alerts.yaml`

## Alerts Configured

### Critical Alerts (Immediate Action Required)

1. **TradeEngineHighOrderFailureRate**
   - **Threshold**: > 10% failure rate over 5 minutes
   - **Runbook**: [high-order-failure-rate.md](runbooks/high-order-failure-rate.md)

2. **TradeEngineExcessiveRiskRejections**
   - **Threshold**: > 5 rejections per minute
   - **Runbook**: [excessive-risk-rejections.md](runbooks/excessive-risk-rejections.md)

3. **TradeEngineExtremeExecutionLatency**
   - **Threshold**: P99 latency > 10 seconds
   - **Runbook**: [extreme-execution-latency.md](runbooks/extreme-execution-latency.md)

### Warning Alerts (Monitor Closely)

4. **TradeEngineDailyLossApproachingLimit**
   - **Threshold**: > 80% of daily loss limit
   - **Runbook**: [daily-loss-approaching-limit.md](runbooks/daily-loss-approaching-limit.md)

5. **TradeEnginePositionSizeNearLimit**
   - **Threshold**: > 90% of position size limit
   - **Runbook**: [position-size-near-limit.md](runbooks/position-size-near-limit.md)

6. **TradeEngineOrderSuccessRateDegradation**
   - **Threshold**: < 95% success rate over 15 minutes
   - **Runbook**: [order-success-rate-degradation.md](runbooks/order-success-rate-degradation.md)

## Deployment

```bash
# Apply alert rules
kubectl --kubeconfig=k8s/kubeconfig.yaml apply -f petrosa_k8s/k8s/monitoring/prometheus-rules/tradeengine-business-alerts.yaml

# Verify rules loaded
kubectl --kubeconfig=k8s/kubeconfig.yaml get prometheusrules -n petrosa-apps | grep tradeengine

# Check specific rule
kubectl --kubeconfig=k8s/kubeconfig.yaml describe prometheusrule tradeengine-business-alerts -n petrosa-apps
```

## Required Metrics

All alerts require the following metrics to be exposed by tradeengine:

### Existing Metrics (Already Deployed)
- ✅ `tradeengine_orders_executed_by_type_total` - Counter
- ✅ `tradeengine_order_failures_total` - Counter
- ✅ `tradeengine_order_execution_latency_seconds` - Histogram
- ✅ `tradeengine_risk_rejections_total` - Counter
- ✅ `tradeengine_total_daily_pnl_usd` - Gauge
- ✅ `tradeengine_current_position_size` - Gauge

### Metrics That May Need to Be Exposed

⚠️ **IMPORTANT**: The following metrics are referenced in alerts but may not currently be exposed. They need to be added to `tradeengine/metrics.py` if they don't exist:

1. **`tradeengine_daily_loss_limit_usd`** (Gauge)
   - **Used by**: `TradeEngineDailyLossApproachingLimit` alert
   - **Description**: Current daily loss limit in USD
   - **Action**: Expose as gauge metric from configuration

2. **`tradeengine_position_size_limit`** (Gauge)
   - **Used by**: `TradeEnginePositionSizeNearLimit` alert
   - **Description**: Current position size limit per symbol
   - **Action**: Expose as gauge metric from configuration

**Alternative**: If these metrics cannot be exposed, the alert queries can be adjusted to use configuration values or removed until metrics are available.

## Notification Channels

Alerts are routed based on severity:

- **Critical**: Immediate notification (PagerDuty, SMS, Slack)
- **Warning**: Slack channel notification, email

### Configuration

Notification channels are configured in Alertmanager. To configure:

1. **Slack Webhook**:
   - Channel: `#petrosa-alerts`
   - Format: Alert name, severity, description, runbook link
   - Mention: `@oncall` for critical alerts

2. **Email**:
   - Distribution list: `petrosa-trading-oncall@company.com`
   - Subject format: `[SEVERITY] Service: Alert Name`

3. **PagerDuty** (Optional):
   - Service: `Petrosa Trading Engine`
   - Integration: Prometheus/Grafana
   - Escalation: Immediate for critical, delayed for warnings

## Testing Alerts

### Test High Order Failure Rate

```bash
# Simulate high failure rate (requires test environment)
# Inject failures or stop exchange connectivity temporarily
# Alert should fire after 2 minutes
```

### Test Risk Rejections

```bash
# Configure very conservative risk limits
# Send orders that will be rejected
# Alert should fire after 2 minutes
```

### Test Execution Latency

```bash
# Simulate high latency (requires test environment)
# Add artificial delays or resource constraints
# Alert should fire after 5 minutes
```

### Test Daily Loss Limit

```bash
# Monitor daily PnL approaching limit
# Alert should fire when > 80% of limit
```

### Test Position Size Limit

```bash
# Monitor position sizes approaching limit
# Alert should fire when > 90% of limit
```

### Test Order Success Rate

```bash
# Monitor order success rate
# Alert should fire when < 95% over 15 minutes
```

## Validation

After deployment, validate:

1. **Rules Loaded**: Check Prometheus UI for loaded rules
2. **Metrics Available**: Verify all required metrics are being scraped
3. **Test Alerts**: Trigger test alerts to verify notifications
4. **Runbook Access**: Verify all runbook links are accessible
5. **Team Notification**: Ensure team is aware of new alerts

## Troubleshooting

### Alerts Not Firing

1. Check if metrics exist: `curl http://petrosa-tradeengine-service:9090/metrics | grep tradeengine_`
2. Verify Prometheus is scraping: Check Prometheus targets
3. Check alert rule syntax: `kubectl describe prometheusrule tradeengine-business-alerts`
4. Review Prometheus logs for evaluation errors

### False Positives

1. Review alert thresholds - may need adjustment
2. Check for data quality issues
3. Review evaluation intervals and "for" durations
4. Consider adding additional filters to queries

### Missing Metrics

1. Verify metrics are exposed: Check `/metrics` endpoint
2. Check metric names match exactly (case-sensitive)
3. Verify labels match query filters
4. Check if metrics need to be added to codebase

## Related Documentation

- [Business Metrics Documentation](BUSINESS_METRICS.md)
- [Runbooks Directory](runbooks/)
- [Prometheus Alert Rules README](../../petrosa_k8s/k8s/monitoring/prometheus-rules/README.md)
