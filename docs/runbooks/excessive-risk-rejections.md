# Runbook: Excessive Risk Rejections

## Alert Details

- **Alert Name**: `TradeEngineExcessiveRiskRejections`
- **Severity**: Critical
- **Threshold**: > 5 rejections per minute
- **Evaluation Interval**: 30 seconds
- **For Duration**: 2 minutes

## Symptom

Risk management system is rejecting orders at a high rate (> 5 rejections per minute), indicating potential misconfiguration or approaching risk limits.

## Impact

- **Trading Disruption**: Valid trading signals are being rejected
- **Missed Opportunities**: Profitable trades may be blocked
- **System Misconfiguration**: Risk parameters may be too conservative
- **Operational Inefficiency**: Team must investigate and adjust parameters

## Investigation Steps

### 1. Identify Rejection Reasons

```bash
# Check rejection reasons breakdown
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_risk_rejections_total

# Query Prometheus for rejection reasons
sum by (reason) (rate(tradeengine_risk_rejections_total[5m]))
```

### 2. Review Risk Parameters

```bash
# Check current risk configuration
curl -X GET http://tradeengine:8080/api/v1/config/trading \
  -H "Content-Type: application/json" | jq '.risk_management'

# Check position limits
curl -X GET http://tradeengine:8080/api/v1/config/trading \
  -H "Content-Type: application/json" | jq '.position_limits'
```

### 3. Check Current Positions

```bash
# Check current position sizes
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_current_position_size

# Query Prometheus for position sizes
tradeengine_current_position_size
```

### 4. Review Daily PnL

```bash
# Check daily PnL
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep tradeengine_total_daily_pnl_usd

# Query Prometheus
tradeengine_total_daily_pnl_usd
```

### 5. Review Signal Sources

```bash
# Check recent signals that were rejected
# Review NATS message flow
# Check signal quality and confidence levels
```

## Resolution Steps

### Immediate Actions

1. **Identify Primary Rejection Reason**: Check which reason is most common
   - `position_limits_exceeded`: Position size too large
   - `daily_loss_limits_exceeded`: Daily loss limit reached
   - `portfolio_exposure_exceeded`: Total exposure too high
   - `concurrent_positions_exceeded`: Too many open positions

2. **Review Risk Parameters**: Verify if limits are appropriate
   ```bash
   # Get current configuration
   curl -X GET http://tradeengine:8080/api/v1/config/trading
   ```

### Common Fixes

#### Position Limits Too Conservative

```bash
# Adjust position size limits (if appropriate)
curl -X PUT http://tradeengine:8080/api/v1/config/trading \
  -H "Content-Type: application/json" \
  -d '{
    "position_size_pct": 0.15,
    "max_position_size_usd": 1500.0
  }'
```

**⚠️ WARNING**: Only adjust if market conditions and risk assessment justify it.

#### Daily Loss Limit Approaching

- Review current daily PnL
- If losses are legitimate (not due to bugs), consider:
  - Accepting the limit (trading will pause automatically)
  - Temporarily increasing limit (with proper authorization)
  - Closing unprofitable positions to free up limit

#### Position Accumulation

- Check if positions are accumulating without proper exits
- Verify stop-loss and take-profit orders are active
- Review position management logic

#### Configuration Errors

- Verify risk parameters are correctly configured
- Check for typos or incorrect units (percentages vs absolute values)
- Review recent configuration changes

## Escalation

**When to Escalate**:
- Rejection rate > 10 per minute for > 10 minutes
- Daily loss limit reached (trading halted)
- Position limits preventing all trading
- Configuration changes require approval

**Escalation Path**:
1. **Trading Team Lead**: For risk parameter adjustments
2. **Risk Management Team**: For limit increases
3. **On-Call Engineer**: If system misconfiguration suspected

## Prevention

1. **Regular Parameter Review**: Review risk parameters weekly
2. **Market Condition Monitoring**: Adjust parameters based on volatility
3. **Position Monitoring**: Track position sizes and daily PnL trends
4. **Configuration Validation**: Validate all risk parameter changes
5. **Alert Tuning**: Set up warning alerts before critical thresholds

## Related Documentation

- [Risk Management Configuration](../CONFIG_API_QUICK_REFERENCE.md)
- [Business Metrics Documentation](../BUSINESS_METRICS.md)
- [Trading Engine Documentation](../TRADING_ENGINE_DOCUMENTATION.md)

## Dashboard Links

- **Grafana Dashboard**: https://grafana.company.com/d/trade-execution
- **Risk Rejections Panel**: Review rejection reasons breakdown
- **Position Monitoring**: Track position sizes and limits
