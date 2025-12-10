# Runbook: Daily Loss Approaching Limit

## Alert Details

- **Alert Name**: `TradeEngineDailyLossApproachingLimit`
- **Severity**: Warning
- **Threshold**: > 80% of daily loss limit
- **Evaluation Interval**: 30 seconds
- **For Duration**: 5 minutes

## Symptom

Daily PnL is approaching the configured daily loss limit (> 80% of limit), indicating potential trading halt if limit is reached.

## Impact

- **Trading Halt Risk**: System will automatically pause trading if limit reached
- **Reduced Trading Flexibility**: Cannot open new positions until limit resets
- **Risk Management**: System is functioning as designed to prevent excessive losses

## Investigation Steps

### 1. Check Current Daily PnL

```bash
# Query current daily PnL
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/petrosa-tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_total_daily_pnl_usd

# Query Prometheus
tradeengine_total_daily_pnl_usd
```

### 2. Check Daily Loss Limit Configuration

```bash
# Get risk configuration
curl -X GET http://petrosa-tradeengine-service:80/api/v1/config/trading \
  -H "Content-Type: application/json" | jq '.risk_management.max_daily_loss_pct'

# Calculate limit in USD (if needed)
# Limit = Portfolio Value * max_daily_loss_pct
```

### 3. Review Active Positions

```bash
# Check unrealized PnL
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/petrosa-tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_total_unrealized_pnl_usd

# Check position sizes
tradeengine_current_position_size
```

### 4. Review Recent Trading Activity

```bash
# Check recent order execution
# Review realized PnL trends
# Check signal quality and success rate
```

### 5. Analyze Loss Sources

```bash
# Review failed trades
# Check if losses are from:
# - Legitimate market movements
# - System bugs or errors
# - Misconfigured strategies
# - Exchange issues
```

## Resolution Steps

### Immediate Actions

1. **Monitor Closely**: Watch for continued losses approaching 100%
   ```bash
   # Set up continuous monitoring
   # Monitor from within pod
   kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/petrosa-tradeengine -n petrosa-apps -- \
     watch -n 30 'curl -s http://localhost:9090/metrics | grep tradeengine_total_daily_pnl_usd'
   ```

2. **Review Active Positions**: Assess if positions should be closed
   - Check unrealized PnL
   - Review market conditions
   - Consider closing unprofitable positions

3. **Reduce Exposure**: Consider reducing position sizes
   ```bash
   # Adjust position sizing (if appropriate)
   curl -X PUT http://petrosa-tradeengine-service:80/api/v1/config/trading \
     -H "Content-Type: application/json" \
     -d '{"position_size_pct": 0.05}'
   ```

### Common Scenarios

#### Legitimate Market Losses

- **Action**: Monitor closely, let risk management work
- **Consideration**: This is expected behavior - system is protecting capital
- **Next Steps**: Review strategy performance, consider strategy adjustments

#### System Bugs Causing Losses

- **Action**: Investigate immediately, pause trading if necessary
- **Check**: Review error logs, check for order execution issues
- **Fix**: Address bugs, verify fixes before resuming

#### Misconfigured Strategies

- **Action**: Review strategy parameters
- **Check**: Verify strategy logic, check signal quality
- **Fix**: Adjust strategy parameters or disable problematic strategies

#### Exchange Issues

- **Action**: Check exchange status
- **Check**: Verify if exchange issues caused losses
- **Fix**: Wait for exchange resolution, consider compensation

## Escalation

**When to Escalate**:
- Loss > 90% of limit (imminent halt)
- Losses due to system bugs
- Unusual loss patterns detected
- Trading halt occurs

**Escalation Path**:
1. **Trading Team Lead**: For strategy review and decisions
2. **Risk Management Team**: For limit adjustments (if needed)
3. **On-Call Engineer**: For system bugs or issues
4. **Management**: If significant financial impact

## Prevention

1. **Daily PnL Monitoring**: Regular review of daily PnL trends
2. **Strategy Performance Review**: Weekly review of strategy profitability
3. **Risk Parameter Tuning**: Adjust limits based on market conditions
4. **Early Warning Alerts**: Set alerts at 50% and 70% of limit
5. **Position Management**: Regular review of position sizes and exposure

## Trading Halt Behavior

When daily loss limit is reached:
- Trading is automatically paused
- New orders are rejected
- Existing positions remain open (not automatically closed)
- Trading resumes at midnight UTC (daily reset)

## Related Documentation

- [Risk Management Configuration](../CONFIG_API_QUICK_REFERENCE.md)
- [Business Metrics Documentation](../BUSINESS_METRICS.md)
- [Trading Engine Documentation](../TRADING_ENGINE_DOCUMENTATION.md)

## Dashboard Links

- **Grafana Dashboard**: Access via Grafana Cloud or local Grafana instance (configure actual URL in your environment)
- **Daily PnL Panel**: Monitor daily PnL vs limit
- **Position Monitoring**: Review active positions and unrealized PnL
