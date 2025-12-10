# Runbook: Order Success Rate Degradation

## Alert Details

- **Alert Name**: `TradeEngineOrderSuccessRateDegradation`
- **Severity**: Warning
- **Threshold**: < 95% success rate over 15 minutes
- **Evaluation Interval**: 30 seconds
- **For Duration**: 15 minutes

## Symptom

Order success rate (filled orders / total orders) has dropped below 95% over a 15-minute window, indicating potential issues with order execution quality.

## Impact

- **Reduced Trading Efficiency**: Orders are not executing successfully
- **Missed Opportunities**: Profitable trades may be missed
- **Customer Experience**: Trading system appears unreliable
- **Financial Impact**: Reduced profitability from failed orders

## Investigation Steps

### 1. Check Order Failure Reasons

```bash
# Query failure reasons
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_order_failures_total

# Query Prometheus for failure breakdown
sum by (failure_reason) (rate(tradeengine_order_failures_total[15m]))
```

### 2. Review Market Conditions

```bash
# Check market volatility
# Review recent price movements
# Check if high volatility is causing order rejections
```

### 3. Check Order Types

```bash
# Review order type distribution
sum by (order_type) (rate(tradeengine_orders_executed_by_type_total[15m]))

# Check if specific order types are failing more
```

### 4. Verify Exchange Status

```bash
# Check Binance status page
# Verify exchange API is operational
# Check for known exchange issues
```

### 5. Review Order Execution Patterns

```bash
# Check order execution logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine --tail=200 | \
  grep -iE "order.*reject|order.*fail|insufficient.*balance|price.*reject"
```

## Resolution Steps

### Immediate Actions

1. **Identify Primary Failure Reason**: Check which reason is most common
   - `insufficient_balance`: Account balance too low
   - `price_rejected`: Order price outside acceptable range
   - `quantity_rejected`: Order quantity invalid
   - `exchange_error`: Exchange API errors
   - `timeout`: Order execution timeout

2. **Review Order Failure Trends**: Check if failures are increasing or stable
   ```bash
   # Check failure rate trend
   rate(tradeengine_order_failures_total[15m])
   ```

### Common Fixes

#### Insufficient Balance

- **Symptom**: Orders rejected due to insufficient account balance
- **Fix**:
  - Check account balance
  - Review position sizes
  - Consider reducing position sizes
  - Verify margin requirements

#### Price Rejection

- **Symptom**: Orders rejected due to price being outside acceptable range
- **Fix**:
  - Review price validation logic
  - Check if market volatility is causing price gaps
  - Consider using market orders instead of limit orders
  - Adjust price tolerance parameters

#### Quantity Rejection

- **Symptom**: Orders rejected due to invalid quantity
- **Fix**:
  - Review quantity calculation logic
  - Check exchange minimum/maximum quantity requirements
  - Verify quantity rounding logic
  - Review position sizing calculations

#### Exchange API Errors

- **Symptom**: Orders rejected due to exchange API issues
- **Fix**:
  - Check exchange status
  - Verify API connectivity
  - Check API rate limits
  - Review API error responses

#### High Market Volatility

- **Symptom**: Orders failing due to rapid price movements
- **Fix**:
  - Consider using market orders during high volatility
  - Adjust price tolerance
  - Implement price slippage protection
  - Consider pausing trading during extreme volatility

## Escalation

**When to Escalate**:
- Success rate < 90% for > 30 minutes
- Multiple failure reasons indicating systemic issues
- Exchange API completely unavailable
- Financial impact is significant

**Escalation Path**:
1. **On-Call Engineer**: For immediate investigation
2. **Trading Team Lead**: For strategy adjustments
3. **Exchange Support**: For exchange API issues
4. **Development Team**: For code bugs

## Prevention

1. **Order Quality Monitoring**: Regular review of success rates
2. **Market Condition Awareness**: Adjust order types based on volatility
3. **Exchange Status Monitoring**: Subscribe to exchange status updates
4. **Order Validation**: Comprehensive validation before order submission
5. **Retry Logic**: Implement smart retry for transient failures

## Related Documentation

- [Business Metrics Documentation](../BUSINESS_METRICS.md)
- [Order Execution Guide](../TRADING_ENGINE_DOCUMENTATION.md)
- [Binance API Integration](../BINANCE_FUTURES_VALIDATION_REPORT.md)

## Dashboard Links

- **Grafana Dashboard**: https://grafana.company.com/d/trade-execution
- **Order Success Rate Panel**: Monitor success rate trends
- **Order Failures Panel**: Review failure reasons breakdown
