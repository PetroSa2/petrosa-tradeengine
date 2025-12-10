# Runbook: High Order Failure Rate

## Alert Details

- **Alert Name**: `TradeEngineHighOrderFailureRate`
- **Severity**: Critical
- **Threshold**: > 10% failure rate over 5 minutes
- **Evaluation Interval**: 30 seconds
- **For Duration**: 2 minutes

## Symptom

Order failure rate exceeds 10% of total orders executed over a 5-minute window. This indicates significant degradation in order execution reliability.

## Impact

- **Trading System Degradation**: Orders are not executing successfully
- **Lost Trading Opportunities**: Signals are generated but orders fail
- **Potential Financial Loss**: Missed profitable trades or inability to exit positions
- **Customer Impact**: Trading system appears unreliable

## Investigation Steps

### 1. Verify Alert Condition

```bash
# Check current failure rate
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep -E "tradeengine_order_failures_total|tradeengine_orders_executed_by_type_total"

# Query Prometheus directly (if accessible)
rate(tradeengine_order_failures_total[5m]) / rate(tradeengine_orders_executed_by_type_total[5m])
```

### 2. Check Exchange Connectivity

```bash
# Test Binance API connectivity
curl -X GET "https://api.binance.com/api/v3/ping"

# Check exchange status
curl -X GET "https://api.binance.com/api/v3/exchangeInfo" | jq '.status'

# Verify API keys are valid
curl -X GET "https://api.binance.com/api/v3/account" \
  -H "X-MBX-APIKEY: $BINANCE_API_KEY" \
  -H "X-MBX-SIGNATURE: $SIGNATURE"
```

### 3. Review Recent Order Logs

```bash
# Check recent order failures in logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine --tail=200 | \
  grep -iE "order.*fail|error.*order|rejected"

# Check MongoDB for failed orders
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  python -c "
from tradeengine.db.mongodb import get_mongodb_client
client = get_mongodb_client()
db = client.petrosa
failures = list(db.orders.find({'status': 'failed'}).sort('timestamp', -1).limit(10))
for f in failures:
    print(f\"Order {f['order_id']}: {f.get('error', 'Unknown error')}\")
"
```

### 4. Check NATS Message Queue

```bash
# Verify NATS connectivity
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  nc -zv nats-server.nats 4222

# Check for stuck messages (if NATS monitoring available)
# Review NATS consumer lag metrics
```

### 5. Verify API Keys and Rate Limits

```bash
# Check if API keys are rate-limited
# Review Binance API rate limit headers in recent requests
# Check for 429 (Too Many Requests) errors in logs
```

## Resolution Steps

### Immediate Actions

1. **If failure rate > 20%**: Consider pausing trading temporarily
   ```bash
   # Disable trading via config API (if available)
   curl -X PUT http://tradeengine:8080/api/v1/config/trading \
     -H "Content-Type: application/json" \
     -d '{"enabled": false}'
   ```

2. **Check for Exchange Maintenance**: Visit Binance status page
   - If exchange is in maintenance, wait for completion
   - If exchange is down, pause trading until restored

3. **Review Recent Deployments**: Check if recent code changes introduced issues
   ```bash
   # Check recent deployments
   kubectl --kubeconfig=k8s/kubeconfig.yaml rollout history deployment/tradeengine -n petrosa-apps
   ```

### Common Fixes

#### Exchange API Connectivity Issues

```bash
# Restart tradeengine pods to refresh connections
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/tradeengine -n petrosa-apps

# Verify pods are healthy
kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=tradeengine
```

#### API Key Issues

- Verify API keys are valid and not expired
- Check API key permissions (trading enabled)
- Verify IP whitelist (if configured)
- Regenerate API keys if necessary

#### Network Issues

- Check cluster network policies allow egress to Binance
- Verify DNS resolution for api.binance.com
- Check firewall rules

#### Invalid Order Parameters

- Review order validation logic
- Check if recent config changes introduced invalid parameters
- Verify order size, price, and type are within exchange limits

## Escalation

**When to Escalate**:
- Failure rate > 30% for > 10 minutes
- Exchange API completely unavailable
- Multiple services affected
- Financial impact is significant

**Escalation Path**:
1. **On-Call Engineer**: Immediate notification via PagerDuty
2. **Trading Team Lead**: If trading needs to be paused
3. **DevOps Team**: If infrastructure issues suspected
4. **Exchange Support**: If Binance API issues confirmed

## Prevention

1. **Monitor Exchange Status**: Subscribe to Binance status updates
2. **API Rate Limit Monitoring**: Track rate limit usage
3. **Health Checks**: Implement comprehensive health checks
4. **Circuit Breakers**: Enable circuit breakers for exchange API calls
5. **Retry Logic**: Implement exponential backoff for failed orders
6. **Alert Tuning**: Adjust thresholds based on historical patterns

## Related Documentation

- [Business Metrics Documentation](../BUSINESS_METRICS.md)
- [Trading Engine Troubleshooting](../TRADING_ENGINE_DOCUMENTATION.md)
- [Binance API Integration](../BINANCE_FUTURES_VALIDATION_REPORT.md)

## Dashboard Links

- **Grafana Dashboard**: https://grafana.company.com/d/trade-execution
- **Prometheus Alerts**: Check Alertmanager UI
- **Order Execution Metrics**: Review `tradeengine_order_failures_total` by reason
