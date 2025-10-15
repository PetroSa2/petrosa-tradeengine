# Complete Verification Guide - All Three Signals

**PR #72**: ✅ Deployed successfully
**Watchdog**: Active in all pods
**Status**: Ready to verify

---

## Quick Verification (Run This)

```bash
# Get a pod
cd /Users/yurisa2/petrosa/petrosa-tradeengine
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')

echo "=== 1. Check Watchdog Started ==="
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps $POD | grep "watchdog started"

echo -e "\n=== 2. Check Handler Attached ==="
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- python -c "import logging; print(f'Handlers: {len(logging.getLogger().handlers)}')"

echo -e "\n=== 3. Send Test Traffic ==="
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/health
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/version

echo -e "\n✅ Test traffic sent"
echo "Wait 30 seconds then check Grafana Cloud"
```

---

## Detailed Verification

### 1. Check Pods Are New

```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine
```

**Look for**: Pods created recently (after 17:13 UTC)

### 2. Verify Watchdog Started

```bash
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps $POD | tail -100
```

**Expected in logs**:
```
✅ OpenTelemetry logging export configured for tradeengine
✅ OTLP logging handler attached to root logger
   Total handlers: 1
...
✅ Trading engine startup completed successfully
✅ OTLP logging handler watchdog started  ← KEY!
```

### 3. Check Handler is Attached RIGHT NOW

```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- python -c "
import logging
print(f'Handlers: {len(logging.getLogger().handlers)}')
for h in logging.getLogger().handlers:
    print(f'  {type(h).__name__}')
"
```

**Expected**:
```
Handlers: 1
  LoggingHandler
```

### 4. Monitor Watchdog Activity

```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=petrosa-tradeengine -f | grep -E "watchdog|handler"
```

**If you see**: "OTLP logging handler was removed, re-attached by watchdog"
**Means**: Watchdog is working, catching handler removals!

### 5. Verify in Grafana Cloud

🔗 https://yurisa2.grafana.net

#### Metrics (Should Already Work)
**Navigate to**: Explore → Prometheus

**Query**:
```promql
up{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}
```

**Expected**: Should show metrics flowing

#### Traces (Should Already Work)
**Navigate to**: Explore → Tempo

**Query**:
```traceql
{service.name="tradeengine"}
```

**Expected**: Should show traces

#### Logs (THE KEY TEST!)
**Navigate to**: Explore → Loki

**Query**:
```logql
{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}
```

**Expected - CONTINUOUS logs**:
- ✅ Initialization logs
- ✅ MongoDB connection logs
- ✅ Binance exchange logs
- ✅ NATS consumer logs
- ✅ Health check logs
- ✅ **Ongoing activity logs** (not just startup!)

---

## What Success Looks Like

### In Grafana Cloud Loki

**Time**: Last 15 minutes
**Query**: `{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}`

**You should see**:
1. Startup sequence (initialization)
2. Repeated health check logs (every few seconds)
3. Any trading activity
4. **Logs continue flowing, not just startup!**

**Key difference from before**:
- Before: Only initialization logs
- After: **Continuous logs throughout pod lifetime**

### In Pod

```bash
# Check handler every minute for 5 minutes
for i in {1..5}; do
  echo "Check $i:"
  kubectl exec -n petrosa-apps $POD -- python -c "import logging; print(f'Handlers: {len(logging.getLogger().handlers)}')"
  sleep 60
done
```

**Expected**: Should always show `Handlers: 1`
**If handler disappears**: Watchdog will re-attach within 30 seconds

---

## If Logs Still Don't Appear

Run this diagnostic:

```bash
# 1. Check if watchdog is running
kubectl logs -n petrosa-apps $POD | grep "watchdog started"

# 2. Check if watchdog is re-attaching (sign of the problem)
kubectl logs -n petrosa-apps $POD | grep "re-attached by watchdog"

# 3. Manually re-attach and send test
kubectl exec -n petrosa-apps $POD -- python << 'EOF'
import otel_init
import logging
import time

otel_init.ensure_logging_handler()
logger = logging.getLogger("manual_check")
logger.setLevel(logging.INFO)
logger.info(f"MANUAL CHECK LOG - {time.time()}")
time.sleep(10)
print("Check Grafana Loki for 'MANUAL CHECK LOG'")
EOF
```

If manual test shows logs in Grafana Cloud → watchdog is working, just need to wait

---

## Timeline

- **17:13**: PR #72 merged
- **17:15**: CI/CD started
- **17:35**: Build completed
- **17:37**: Deployment completed ✅
- **17:40**: **Verify now!**

---

## Success Criteria

✅ **All Three Signals Working**:
- Metrics in Prometheus ✅
- Traces in Tempo ✅
- Logs in Loki ✅ (continuous!)

**The watchdog ensures logs keep flowing!** 🚀
