# Logs Not Appearing - Troubleshooting Guide

## ✅ What's Confirmed Working

1. ✅ **Application Generating Logs**: Rich structured logs with trace_id
2. ✅ **OTLP Log Export Enabled**: `✅ OpenTelemetry logging export enabled for tradeengine`
3. ✅ **Network Connectivity**: grafana-alloy.observability:4317 open
4. ✅ **Grafana Alloy OTLP Receiver**: Running on ports 4317/4318
5. ✅ **OTLP Exporter Configured**: Basic auth to Grafana Cloud
6. ✅ **No Errors**: No export failures in application or Alloy logs

## ❌ Problem

Logs not appearing in Grafana Cloud Loki despite everything being configured.

## 🔍 Possible Causes

### 1. OTLP Logs Go to Different Datasource

OTLP logs might go to a different Loki datasource than Kubernetes logs.

**Try in Grafana Cloud**:
- Check ALL Loki datasources (not just the default one)
- Look for datasource named "OTLP Logs" or "Grafana Cloud Logs"

### 2. Label Mismatch

OTLP logs may have different labels than Kubernetes logs.

**Try these queries**:
```logql
# All logs (no filters)
{}

# By service name (OTLP attribute)
{service_name="tradeengine"}

# By job (might be different)
{job=~".*tradeengine.*"}

# All OTLP logs
{exporter="OTLP"}
```

### 3. Time Synchronization

OTLP logs might have different timestamps.

**Try**:
- Change time range to "Last 6 hours" or "Last 24 hours"
- Check if timestamps are in the future/past

### 4. Sampling/Filtering

Logs might be sampled or filtered out.

**Check**:
- Grafana Alloy config has rate limiting (1000 lines/sec)
- Debug logs are dropped
- Might be under the limit but batched

### 5. Silent Authentication Issue

Auth might be working for traces/metrics but not logs.

**Evidence**: No 401 errors, but logs still not appearing

## 🎯 Debugging Steps

### Step 1: Check Grafana Alloy is Receiving OTLP Data

```bash
# Check for any OTLP activity
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n observability -l app=grafana-alloy --since=10m | grep -i "received\|batch\|export"
```

**Expected**: Should see batch exports or received items

### Step 2: Generate Obvious Log Traffic

```bash
# Generate unique logs
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')

# Hit different endpoints to create varied logs
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/health
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/version
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/account
```

Wait 30 seconds, then search in Loki for recent activity.

### Step 3: Check Loki Directly via API

Test if logs are in Loki but just not visible in UI:

```bash
# Query Loki API directly
curl -G -s "https://logs-prod-024.grafana.net/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="petrosa-apps"}' \
  --data-urlencode 'limit=10' \
  -u "1360689:glc_..." | jq '.data.result[].stream'
```

### Step 4: Check OTLP Logs Specifically

OTLP logs might have different label structure:

```logql
# Search by all possible OTLP labels
{__name__=~".+"}
{service_name=~".+"}
{resource_service_name=~".+"}
```

## 🚀 Alternative: Use Loki Push API Directly

Since OTLP isn't showing logs, we could configure a Loki handler instead:

```python
# Install python-logging-loki
# In otel_init.py, add:
from logging_loki import LokiHandler

loki_handler = LokiHandler(
    url="https://logs-prod-024.grafana.net/loki/api/v1/push",
    tags={"service": "tradeengine"},
    auth=("1360689", "token"),
    version="1",
)
logging.getLogger().addHandler(loki_handler)
```

This bypasses OTLP entirely and sends directly to Loki.

## 📊 What to Check in Grafana Cloud

1. **Check ALL Loki datasources** (might be multiple)
2. **Try query**: `{service_name="tradeengine"}`
3. **Try query**: `{job=~".*"}` (see all jobs)
4. **Check "Explore" → "Logs" tab** (not Loki datasource)
5. **Time range**: Last 6-24 hours (in case timestamps are off)

## 🎓 Most Likely Issues

Based on experience:

1. **Most likely**: Logs are there but with different labels than expected
2. **Second**: OTLP logs go to different datasource
3. **Third**: Time range issue (logs timestamped differently)
4. **Least likely**: Silent authentication failure for logs only

## 📝 Action Plan

1. Try broader queries in Loki (no filters)
2. Check all available Loki datasources
3. Expand time range significantly
4. If still nothing, consider direct Loki push API instead of OTLP

---

**The infrastructure is correct - just need to find where the logs are appearing!**
