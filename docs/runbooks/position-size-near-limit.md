# Runbook: Position Size Near Limit

## Alert Details

- **Alert Name**: `TradeEnginePositionSizeNearLimit`
- **Severity**: Warning
- **Threshold**: > 90% of position size limit
- **Evaluation Interval**: 30 seconds
- **For Duration**: 5 minutes

## Symptom

Current position size for a symbol is approaching the configured position size limit (> 90% of limit), which may prevent opening new positions in the same direction.

## Impact

- **Trading Flexibility Reduced**: Cannot open new positions in same direction
- **Risk Concentration**: Large position size increases risk
- **Order Rejections**: New orders may be rejected due to limit

## Investigation Steps

### 1. Check Current Position Size

```bash
# Query current position sizes
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/petrosa-tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_current_position_size

# Query Prometheus for specific symbol
tradeengine_current_position_size{symbol="BTCUSDT"}
```

### 2. Check Position Size Limit

```bash
# Get position size configuration
curl -X GET http://petrosa-tradeengine-service:80/api/v1/config/trading \
  -H "Content-Type: application/json" | jq '.position_limits'

# Check symbol-specific limits
curl -X GET http://tradeengine:8080/api/v1/config/trading/BTCUSDT \
  -H "Content-Type: application/json" | jq '.position_size_pct, .max_position_size_usd'
```

### 3. Review Position History

```bash
# Check how position grew to current size
# Review recent order executions
# Check if position accumulated without proper exits
```

### 4. Verify Stop-Loss/Take-Profit Orders

```bash
# Check if stop-loss orders are active
# Verify take-profit orders are set
# Review conditional order status
```

### 5. Check Market Conditions

```bash
# Review recent market movements
# Check if position is profitable or unprofitable
# Assess if position should be reduced
```

## Resolution Steps

### Immediate Actions

1. **Monitor Position Closely**: Watch for continued growth toward limit
   ```bash
   # Continuous monitoring
   watch -n 30 'curl -s http://tradeengine:9090/metrics | grep tradeengine_current_position_size'
   ```

2. **Avoid Opening New Positions**: In same direction until position reduced
   - System will automatically reject orders that would exceed limit
   - Manual intervention may be needed to prevent order attempts

3. **Consider Partial Closure**: If market conditions allow
   - Review unrealized PnL
   - Assess market conditions
   - Consider closing portion of position

### Common Scenarios

#### Position Accumulation

- **Symptom**: Position grew larger than intended
- **Cause**: Multiple orders opened without proper position management
- **Fix**: Review position management logic, implement position size checks

#### Stop-Loss Not Triggered

- **Symptom**: Position should have been closed but remains open
- **Cause**: Stop-loss order not executed or not set
- **Fix**: Verify stop-loss orders are active, check order execution logs

#### Market Movement

- **Symptom**: Position value increased due to favorable price movement
- **Cause**: Normal market behavior
- **Action**: Monitor, consider taking profits if appropriate

#### Configuration Issue

- **Symptom**: Limit is too low for intended trading strategy
- **Cause**: Position size limits misconfigured
- **Fix**: Review and adjust limits (with proper authorization)

## Escalation

**When to Escalate**:
- Position reaches 100% of limit (cannot trade)
- Position accumulation indicates system bug
- Stop-loss orders not working
- Configuration changes require approval

**Escalation Path**:
1. **Trading Team Lead**: For position management decisions
2. **Risk Management Team**: For limit adjustments
3. **On-Call Engineer**: For system bugs or order execution issues

## Prevention

1. **Position Monitoring**: Regular review of position sizes
2. **Stop-Loss Enforcement**: Ensure all positions have stop-loss orders
3. **Position Size Validation**: Check position size before opening new orders
4. **Configuration Review**: Regular review of position size limits
5. **Early Warning Alerts**: Set alerts at 70% and 85% of limit

## Related Documentation

- [Position Management](../HEDGE_MODE_POSITION_TRACKING.md)
- [Risk Management Configuration](../CONFIG_API_QUICK_REFERENCE.md)
- [Business Metrics Documentation](../BUSINESS_METRICS.md)

## Dashboard Links

- **Grafana Dashboard**: Access via Grafana Cloud or local Grafana instance (configure actual URL in your environment)
- **Position Size Panel**: Monitor position sizes vs limits
- **Position Management**: Review position lifecycle
