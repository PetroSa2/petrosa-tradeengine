# Critical Findings - OTLP Logs Mystery

**Date**: October 14, 2025
**Status**: Logs being captured but not appearing in Grafana Cloud

---

## 🔍 What We Know FOR SURE

### ✅ OTLP Handler IS Working
**Evidence**:
- Logs show: "OTLP logging handler attached to root logger, Total handlers: 1"
- Application logs (MongoDB, Binance, NATS, watchdog messages) **NOT in pod stdout**
- This confirms handler IS capturing logs

### ✅ OTLP Exporter Created Successfully
**Evidence**:
- All exporters (traces, metrics, logs) create without errors
- Flush completes without errors
- No exceptions in application logs

### ❌ Logs Never Reach Grafana Alloy
**Evidence**:
- Zero OTLP activity in Grafana Alloy logs
- No batch processing messages
- No export messages
- Nothing

### ❌ Logs Never Appear in Grafana Cloud
**Evidence**:
- Query `{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}` returns nothing
- Only old Kubernetes-collected logs visible (from before OTLP handler)

---

## 🎯 The Mystery

**Application says**: "Flush completed" ✅
**Grafana Alloy says**: Received nothing ❌
**Grafana Cloud says**: No logs ❌

**This is a silent transport failure!**

---

## 💡 Possible Root Causes

### 1. gRPC Connection Issue (Most Likely)
**Theory**: OTLPLogExporter fails to connect but reports success

**Evidence**:
- Metrics/traces work (but maybe use different protocol internally?)
- Logs silently fail
- No errors logged

**Test**: Try HTTP OTLP exporter instead of gRPC

### 2. Different Protocol for Logs
**Theory**: Maybe Grafana Cloud expects logs via different protocol

**Evidence**:
- The Grafana Cloud guide shows using HTTP for OTLP
- We're using gRPC
- Might not be supported for logs

**Test**: Switch to `OTLPLogExporter` from `proto.http` package

### 3. Logs Filtered/Dropped by Grafana Alloy
**Theory**: Alloy receives logs but drops them

**Evidence**:
- Config shows rate limiting (1000 lines/sec)
- Debug logs are dropped
- Maybe all logs being dropped?

**Test**: Check Alloy stats/metrics

### 4. Logs in Grafana Cloud But Wrong Labels
**Theory**: Logs ARE there but with unexpected labels

**Evidence**:
- OTLP attributes might create different labels
- service_name vs namespace/pod
- Different datasource

**Test**: Try ALL queries in Grafana Cloud

---

## 🧪 Tests to Run

### Test 1: Try HTTP OTLP Exporter for Logs

Change from:
```python
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
```

To:
```python
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
```

Use endpoint: `http://grafana-alloy.observability.svc.cluster.local:4318` (HTTP port)

### Test 2: Check ALL Loki Datasources

In Grafana Cloud:
1. Settings → Data Sources
2. List ALL Loki datasources
3. Try query in EACH one

### Test 3: Broadest Possible Query

```logql
{} |= "tradeengine"
```

Or just:
```logql
{}
```

Then manually filter for recent entries

### Test 4: Check Loki Via API

Directly query Loki API:
```bash
curl -G "https://logs-prod-024.grafana.net/loki/api/v1/query_range" \
  -H "Authorization: Basic <token>" \
  --data-urlencode 'query={service_name="tradeengine"}' \
  --data-urlencode 'limit=100'
```

---

## 📊 Next Steps

**BEFORE making more code changes**:

1. **Check Grafana Cloud thoroughly**
   - Try ALL Loki datasources
   - Try query: `{service_name="tradeengine"}`
   - Try query: `{}`
   - Check label browser

2. **If truly no logs**, then try HTTP OTLP exporter
   - This is a configuration change, not code logic
   - Might be what Grafana Cloud expects

3. **Report back what you find**
   - Which queries you tried
   - What labels you see in label browser
   - Any logs at all in any datasource

---

**I need you to check Grafana Cloud with these specific queries to rule out label mismatch before we change more code!**
