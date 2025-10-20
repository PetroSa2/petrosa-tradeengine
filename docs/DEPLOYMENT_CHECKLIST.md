# Hedge Mode Position Tracking - Deployment Checklist

**Implementation Date**: October 16, 2025
**Status**: ✅ Ready for Deployment

## Pre-Deployment Verification

### ✅ Manual Checks Completed

- [x] **Hedge mode enabled on Binance**: Confirmed by user
- [x] **MySQL credentials in K8s secrets**: Verified MYSQL_URI exists in `petrosa-sensitive-credentials`
- [x] **K8s secrets exist**: Confirmed `petrosa-sensitive-credentials` secret exists
- [x] **All code changes verified**: Passed verification script

### ✅ Implementation Verification

Run verification script:
```bash
./scripts/verify-hedge-mode-implementation.sh
```

**Result**: ✅ All checks passed (2 acceptable warnings)

## Deployment Steps

### Step 1: Verify Cluster Access

```bash
export KUBECONFIG=k8s/kubeconfig.yaml
kubectl cluster-info
kubectl get namespace petrosa-apps
```

### Step 2: Verify Secrets

```bash
# Check MySQL URI exists
kubectl get secret petrosa-sensitive-credentials -n petrosa-apps -o jsonpath='{.data.MYSQL_URI}' | base64 -d
echo ""

# Should output MySQL connection string
```

### Step 3: Deploy with MySQL Initialization

```bash
# Use the automated deployment script
./scripts/deploy-with-mysql-init.sh
```

This script will:
1. Run MySQL schema initialization job
2. Wait for job completion
3. Deploy the main application
4. Verify deployment status

**OR** deploy manually:

```bash
# Step 3a: Initialize MySQL schema
kubectl apply -f k8s/mysql-schema-job.yaml
kubectl wait --for=condition=complete --timeout=300s job/petrosa-tradeengine-mysql-schema -n petrosa-apps
kubectl logs -n petrosa-apps job/petrosa-tradeengine-mysql-schema

# Step 3b: Deploy application
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/ingress.yaml

# Step 3c: Wait for rollout
kubectl rollout status deployment/petrosa-tradeengine -n petrosa-apps
```

### Step 4: Verify Deployment

```bash
# Check pods are running
kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine

# Check logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine --tail=100

# Verify MySQL connection
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep "MySQL client connected"
```

### Step 5: Verify Metrics

```bash
# Port forward to access metrics
kubectl port-forward -n petrosa-apps svc/petrosa-tradeengine 8000:80

# In another terminal, check position metrics
curl http://localhost:8000/metrics | grep position
```

Expected metrics:
- `tradeengine_positions_opened_total`
- `tradeengine_positions_closed_total`
- `tradeengine_position_pnl_usd`
- `tradeengine_position_pnl_percentage`
- `tradeengine_position_duration_seconds`
- `tradeengine_position_roi`
- `tradeengine_positions_winning_total`
- `tradeengine_positions_losing_total`

### Step 6: Verify MySQL Schema

```bash
# Get MySQL URI
MYSQL_URI=$(kubectl get secret petrosa-sensitive-credentials -n petrosa-apps -o jsonpath='{.data.MYSQL_URI}' | base64 -d)

# Connect to MySQL (if accessible) and verify
mysql -u [user] -p -h [host] -e "USE petrosa; DESCRIBE positions;"
```

Expected columns:
- position_id, strategy_id, exchange, symbol, position_side
- entry_price, quantity, entry_time
- stop_loss, take_profit, status
- pnl, pnl_after_fees, duration_seconds
- And more...

## Post-Deployment Verification

### Verify Position Tracking

When a position is opened, check:

```bash
# Monitor logs for position creation
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine -f | grep -i "position.*created"

# Check metrics
curl http://localhost:8000/metrics | grep tradeengine_positions_opened_total
```

### Verify Grafana Cloud

1. Log in to Grafana Cloud
2. Navigate to Explore
3. Run queries:
   ```promql
   # Total positions opened
   sum(tradeengine_positions_opened_total)

   # PnL by strategy
   sum by (strategy_id) (tradeengine_position_pnl_usd)

   # Win rate
   sum(tradeengine_positions_winning_total) /
   (sum(tradeengine_positions_winning_total) + sum(tradeengine_positions_losing_total))
   ```

### Test with a Sample Position

```bash
# Send a test signal (if test endpoint exists)
curl -X POST http://localhost:8000/trade \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "test_strategy",
    "symbol": "BTCUSDT",
    "action": "buy",
    "price": 45000.0,
    "confidence": 0.85,
    "timestamp": "2025-10-16T12:00:00Z",
    "meta": {
      "order_type": "limit",
      "base_amount": 0.001,
      "stop_loss": 43000.0,
      "take_profit": 47000.0,
      "simulate": true
    }
  }'

# Check logs for position creation
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine --tail=50 | grep position
```

## Rollback Plan

If issues arise:

```bash
# Rollback deployment
kubectl rollout undo deployment/petrosa-tradeengine -n petrosa-apps

# Check rollback status
kubectl rollout status deployment/petrosa-tradeengine -n petrosa-apps

# Verify previous version is running
kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine
```

## Troubleshooting

### MySQL Connection Issues

```bash
# Check job logs
kubectl logs -n petrosa-apps job/petrosa-tradeengine-mysql-schema

# Check pod logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep -i mysql

# Verify secret
kubectl get secret petrosa-sensitive-credentials -n petrosa-apps -o yaml | grep MYSQL_URI
```

### Metrics Not Appearing

```bash
# Check metrics endpoint
kubectl port-forward -n petrosa-apps svc/petrosa-tradeengine 8000:80
curl http://localhost:8000/metrics

# Check OTLP configuration
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep -i otel
```

### Position Not Being Created

```bash
# Check for errors in logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep -i error

# Check position manager initialization
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep "Position manager initialized"

# Check MySQL client connection
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep "MySQL client connected"
```

## Success Criteria

✅ Deployment is successful when:

1. All pods are in Running state
2. Health checks pass (liveness, readiness, startup)
3. MySQL schema job completed successfully
4. MySQL client connection established
5. Position metrics appear in `/metrics` endpoint
6. Metrics appear in Grafana Cloud
7. No errors in pod logs
8. Test position creates successfully

## Files Changed

### New Files (10)
1. `scripts/create_positions_table.sql`
2. `shared/mysql_client.py`
3. `scripts/verify_hedge_mode.py`
4. `tradeengine/metrics.py`
5. `tests/test_position_tracking.py`
6. `docs/HEDGE_MODE_POSITION_TRACKING.md`
7. `HEDGE_MODE_IMPLEMENTATION_SUMMARY.md`
8. `k8s/mysql-schema-job.yaml`
9. `scripts/deploy-with-mysql-init.sh`
10. `scripts/verify-hedge-mode-implementation.sh`

### Modified Files (6)
1. `contracts/order.py` - Added position tracking fields
2. `tradeengine/exchange/binance.py` - Added positionSide support
3. `tradeengine/dispatcher.py` - Position ID generation
4. `tradeengine/position_manager.py` - Dual persistence + metrics
5. `k8s/deployment.yaml` - Added MYSQL_URI and HEDGE_MODE_ENABLED
6. `shared/constants.py` - Added hedge mode constants

## Support

For issues or questions:

1. Check logs: `kubectl logs -n petrosa-apps -l app=petrosa-tradeengine --tail=200`
2. Check events: `kubectl get events -n petrosa-apps --sort-by='.lastTimestamp' | tail -20`
3. Review documentation: `docs/HEDGE_MODE_POSITION_TRACKING.md`
4. Review implementation summary: `HEDGE_MODE_IMPLEMENTATION_SUMMARY.md`

---

**Deployment Date**: _________________
**Deployed By**: _________________
**Deployment Status**: _________________
**Notes**: _________________
