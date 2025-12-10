# Runbook: Extreme Execution Latency

## Alert Details

- **Alert Name**: `TradeEngineExtremeExecutionLatency`
- **Severity**: Critical
- **Threshold**: P99 latency > 10 seconds
- **Evaluation Interval**: 30 seconds
- **For Duration**: 5 minutes

## Symptom

Order execution latency (time from signal receipt to order completion) exceeds 10 seconds at the 99th percentile, indicating severe performance degradation.

## Impact

- **Missed Trading Opportunities**: Delayed execution results in worse fill prices
- **Slippage**: Market moves during execution delay
- **Customer Experience**: Trading system appears slow and unresponsive
- **Financial Impact**: Poor execution prices reduce profitability

## Investigation Steps

### 1. Check System Resources

```bash
# Check CPU and memory usage
kubectl --kubeconfig=k8s/kubeconfig.yaml top pods -n petrosa-apps -l app=tradeengine

# Check node resources
kubectl --kubeconfig=k8s/kubeconfig.yaml top nodes
```

### 2. Review Database Query Performance

```bash
# Check MongoDB connection pool
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine --tail=100 | \
  grep -iE "mongodb|database|query.*slow"

# Check for slow queries in MongoDB (if accessible)
# Review MongoDB slow query logs
```

### 3. Verify Exchange API Latency

```bash
# Test direct API latency
time curl -X GET "https://api.binance.com/api/v3/ping"

# Check recent API response times in logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=tradeengine --tail=200 | \
  grep -iE "binance.*response|api.*latency"
```

### 4. Check NATS Consumer Lag

```bash
# Check NATS message processing rate
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it deployment/tradeengine -n petrosa-apps -- \
  curl -s http://localhost:9090/metrics | grep -E "nats.*lag|nats.*messages"

# Review NATS consumer metrics
```

### 5. Review Recent Deployments

```bash
# Check deployment history
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout history deployment/tradeengine -n petrosa-apps

# Check if recent changes introduced performance regression
```

## Resolution Steps

### Immediate Actions

1. **Scale Up Resources** (if CPU/memory constrained):
   ```bash
   # Increase CPU/memory limits
   kubectl --kubeconfig=k8s/kubeconfig.yaml set resources deployment/tradeengine \
     --limits=cpu=2000m,memory=4Gi \
     --requests=cpu=1000m,memory=2Gi \
     -n petrosa-apps
   ```

2. **Restart Pods** (if connection pool issues):
   ```bash
   kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/tradeengine -n petrosa-apps
   ```

### Common Fixes

#### High CPU Usage

- Check for CPU-intensive operations
- Review code for inefficient algorithms
- Consider horizontal scaling (if stateless)
- Optimize hot paths in code

#### Database Connection Pool Exhaustion

- Increase MongoDB connection pool size
- Check for connection leaks
- Review query patterns for optimization
- Consider read replicas for read-heavy operations

#### Network Latency to Exchange

- Check network policies allow egress
- Verify DNS resolution
- Consider using exchange WebSocket for real-time data
- Review API call patterns (batch if possible)

#### NATS Message Backlog

- Check NATS consumer processing rate
- Review message processing logic for bottlenecks
- Consider increasing consumer replicas
- Verify NATS server performance

#### Code Performance Issues

- Profile application to identify bottlenecks
- Review recent code changes
- Check for N+1 query problems
- Optimize database queries

## Escalation

**When to Escalate**:
- Latency > 30 seconds for > 10 minutes
- System resources exhausted
- Database performance severely degraded
- Multiple services affected

**Escalation Path**:
1. **On-Call Engineer**: For immediate performance issues
2. **DevOps Team**: For infrastructure/resource issues
3. **Development Team**: For code performance issues
4. **Database Team**: For MongoDB performance issues

## Prevention

1. **Resource Monitoring**: Set up alerts for CPU/memory usage
2. **Performance Testing**: Regular load testing
3. **Database Optimization**: Regular query performance reviews
4. **Code Profiling**: Regular performance profiling
5. **Capacity Planning**: Monitor trends and plan scaling

## Related Documentation

- [Business Metrics Documentation](../BUSINESS_METRICS.md)
- [Performance Optimization Guide](../QUICK_OPTIMIZATION_GUIDE.md)
- [Trading Engine Documentation](../TRADING_ENGINE_DOCUMENTATION.md)

## Dashboard Links

- **Grafana Dashboard**: https://grafana.company.com/d/trade-execution
- **Latency Metrics**: Review p50, p95, p99 latency trends
- **System Resources**: Monitor CPU/memory usage
