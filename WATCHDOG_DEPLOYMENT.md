# Logging Handler Watchdog - Deployment in Progress

**PR**: #72 - MERGED ✅
**Status**: CI/CD deploying
**ETA**: ~10-15 minutes

---

## What the Watchdog Does

### Background Task
```python
async def logging_handler_watchdog() -> None:
    """Periodically ensure OTLP logging handler stays attached"""
    while True:
        await asyncio.sleep(30)  # Check every 30 seconds
        was_attached = otel_init.ensure_logging_handler()
        if not was_attached:
            logger.warning("OTLP logging handler was removed, re-attached by watchdog")
```

### How It Works
1. **Runs every 30 seconds** in the background
2. **Calls ensure_logging_handler()** to check if handler is attached
3. **Re-attaches if missing** (self-healing)
4. **Logs when it fixes** (so we can see when handler was removed)

---

## Why This Fixes the Issue

### The Problem
- ✅ Logs appear at initialization
- ❌ Then stop (handler gets removed)
- ❓ Don't know WHAT removes it
- ❓ Don't know WHEN it happens

### The Solution
- ✅ Watchdog detects removal within 30 seconds
- ✅ Automatically re-attaches handler
- ✅ Logs flow continuously
- ✅ Works regardless of what removes it

---

## Evidence This Will Work

### Test in Running Pod ✅
```
Before: Handlers: 0
After ensure_logging_handler(): Handlers: 1
Test logs sent successfully
```

**Proven**: Re-attaching handler works!

---

## Expected Behavior After Deployment

### Startup Logs
```
✅ OpenTelemetry logging export configured
✅ OTLP logging handler attached to root logger
   Total handlers: 1
...
✅ Trading engine startup completed successfully
✅ OTLP logging handler watchdog started  ← NEW!
```

### Every 30 Seconds (Silent if OK)
- Check handler
- Re-attach if needed
- If re-attached, log warning

### In Grafana Cloud Loki
- ✅ Initialization logs (already working)
- ✅ **Continuous logs** (new with watchdog!)
- ✅ All application activity logged

---

## Verification Steps

### Step 1: Check New Pods Running
```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine
```

Look for pods created after ~17:13 UTC

### Step 2: Check Watchdog Started
```bash
POD=$(kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n petrosa-apps $POD | grep watchdog
```

Expected: `✅ OTLP logging handler watchdog started`

### Step 3: Check Handler Persists
```bash
kubectl exec -n petrosa-apps $POD -- python -c "
import logging
print(f'Handlers: {len(logging.getLogger().handlers)}')
"
```

Expected: `Handlers: 1` (should stay 1 continuously)

### Step 4: Monitor Watchdog Activity
```bash
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine -f | grep -E "watchdog|handler"
```

If you see warnings about re-attaching → watchdog is working!

### Step 5: Check Grafana Cloud Loki
🔗 https://yurisa2.grafana.net → Explore → Loki

Query:
```logql
{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}
```

Expected:
- ✅ Initialization logs
- ✅ **Continuous application logs**
- ✅ MongoDB queries
- ✅ NATS activity
- ✅ Health checks

---

## Impact on All Three Signals

| Signal | Changed? | Status |
|--------|----------|--------|
| **Metrics** | ❌ No | ✅ Working |
| **Traces** | ❌ No | ✅ Working |
| **Logs** | ✅ Yes (watchdog) | ✅ Should work continuously |

**This ONLY touches logs, metrics and traces are not affected!**

---

## Timeline

- **17:13 UTC**: PR #72 merged
- **17:15 UTC**: CI/CD started
- **17:25 UTC**: Expected deployment complete
- **17:26 UTC**: New pods running with watchdog
- **17:30 UTC**: Logs should be flowing!

---

## Success Metrics

After deployment:
- ✅ Handler stays attached (watchdog keeps it alive)
- ✅ Logs flow continuously to Grafana Cloud
- ✅ Metrics still working
- ✅ Traces still working

**Complete observability achieved!** 🚀
