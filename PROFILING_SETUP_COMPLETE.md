# 🔥 Profiling Implementation - Complete Setup Guide

**Date**: October 14, 2025
**PR**: #65 (Merged ✅)
**Status**: 🚀 **CI/CD DEPLOYING**

---

## ✅ What Was Done

### Code Changes (PR #65)
- ✅ Created `profiler_init.py` with Pyroscope configuration
- ✅ Added `pyroscope-io>=0.8.7` to `requirements.txt`
- ✅ Imported profiler in `api.py` (auto-initializes if enabled)
- ✅ Updated `deployment.yaml` with profiler env vars
- ✅ Created `setup-pyroscope-token.sh` helper script
- ✅ Added comprehensive documentation

### Infrastructure Changes
- ✅ Updated `petrosa-common-config` with:
  - `ENABLE_PROFILER=true`
  - `PYROSCOPE_SERVER_ADDRESS=https://profiles-prod-011.grafana.net`

### CI/CD Status
- ✅ All checks passed (Lint, Test, Security)
- 🔄 Building Docker image with profiling support
- ⏳ Will deploy automatically (~5-10 minutes)

---

## ⏳ REQUIRED: Get Pyroscope Token

**The profiler is configured but needs authentication token!**

### Step 1: Get Token from Grafana Cloud

1. Go to: https://grafana.com/orgs/yurisa2/stacks
2. Click on your stack
3. Look for **"Profiles"** or **"Pyroscope"** section
4. Click **"Configure"** or **"Send profiles"**
5. Generate or copy your token

**What you need**:
- Token (starts with `glc_...`)
- User ID (number like 123456)
- Endpoint URL (likely `https://profiles-prod-011.grafana.net` for sa-east-1)

### Step 2: Add Token to Kubernetes Secret

Once you have the token, run:

```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine

# Option A: Use the helper script
export GRAFANA_CLOUD_TOKEN="glc_your_pyroscope_token_here"
bash scripts/setup-pyroscope-token.sh

# Option B: Manual kubectl command
kubectl --kubeconfig=k8s/kubeconfig.yaml get secret petrosa-sensitive-credentials -n petrosa-apps -o json | \
  jq --arg token "glc_your_token_here" '.data.PYROSCOPE_AUTH_TOKEN = ($token | @base64)' | \
  kubectl --kubeconfig=k8s/kubeconfig.yaml apply -f -
```

### Step 3: Restart Pods to Pick Up Token

```bash
# After adding token, restart deployment
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/petrosa-tradeengine -n petrosa-apps

# Wait for restart
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout status deployment/petrosa-tradeengine -n petrosa-apps
```

### Step 4: Verify Profiling is Active

```bash
# Check pod logs for profiler initialization
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps \
  -l app=petrosa-tradeengine --tail=100 | grep -i pyroscope

# Expected output:
# ✅ Pyroscope continuous profiling enabled for tradeengine
#    Server: https://profiles-prod-011.grafana.net
#    Version: 1.1.X
#    Profiling: CPU (oncpu), Sample rate: 100Hz
```

---

## 🔍 View Profiles in Grafana Cloud

### Access Grafana Cloud
🔗 https://yurisa2.grafana.net

### Navigate to Profiles
1. Click **Explore** (compass icon)
2. Select datasource: **Pyroscope** or **profiles**
3. Query: `{service_name="tradeengine"}`
4. Select profile type: **CPU** or **Memory**
5. View flame graph!

### What You'll See

**Flame Graph**: Visual representation of CPU/memory usage
- **Wide bars**: Functions consuming most resources
- **Tall stacks**: Deep call chains
- **Colors**: Different code paths

**Common Patterns**:
- Wide bar in `dispatcher`: Optimization target
- Wide bar in MongoDB calls: Consider caching
- Wide bar in Binance API: Network I/O overhead
- Memory growth: Potential leak

---

## 📊 Complete Observability Stack

You now have ALL FOUR telemetry signals:

| Signal | Status | Purpose |
|--------|--------|---------|
| **Metrics** | ✅ Operational | How much, how fast |
| **Traces** | ✅ Operational | What happened, when |
| **Logs** | 🔄 Deploying (PR #64) | Detailed events |
| **Profiles** | 🔄 Deploying (PR #65) | Why it's slow |

---

## 🎯 Quick Reference

### Check if Profiling is Enabled
```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps \
  $(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}') \
  -- env | grep PROFILER
```

### Generate Profile Traffic
```bash
# Run some requests to create profile data
for i in {1..50}; do
  kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps \
    $(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}') \
    -- wget -q -O- http://localhost:8000/health > /dev/null
  sleep 1
done
```

### View Profiles
```
Grafana Cloud → Explore → Pyroscope
Query: {service_name="tradeengine"}
Profile type: CPU
Time range: Last 15 minutes
```

---

## 🎓 What to Look For

### Performance Optimization Targets

1. **Wide Bars in Business Logic**
   - Look for: `dispatcher.dispatch`, `position_manager`
   - Action: Optimize algorithms, add caching

2. **Database Operations**
   - Look for: `MongoDB` calls in flame graph
   - Action: Add indexes, optimize queries, cache results

3. **External API Calls**
   - Look for: `Binance` API calls
   - Action: Batch requests, add caching, use WebSockets

4. **Memory Leaks**
   - Compare memory profiles over time
   - Look for: Steady growth without load increase
   - Action: Fix unreleased resources

---

## ⚙️ Configuration Options

### Adjust Sampling Rate

Lower overhead, less detailed profiles:
```python
# In profiler_init.py
sample_rate=50,  # 50 samples/second (default: 100)
```

### Enable Additional Profile Types

```python
pyroscope.configure(
    # ... existing config ...
    oncpu=True,         # CPU profiling ✅
    allocation=True,    # Memory allocation tracking
    blocking=True,      # Lock/blocking profiling
)
```

### Disable Profiling Temporarily

```bash
# Update configmap
kubectl --kubeconfig=k8s/kubeconfig.yaml patch configmap petrosa-common-config -n petrosa-apps \
  --type=json -p='[{"op": "replace", "path": "/data/ENABLE_PROFILER", "value": "false"}]'

# Restart pods
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/petrosa-tradeengine -n petrosa-apps
```

---

## 📈 Expected Results

### Overhead
- **CPU**: 1-3% additional CPU usage
- **Memory**: 10-50MB per process
- **Network**: 100-500KB/minute

### Profile Frequency
- **Samples**: 100/second (configurable)
- **Upload**: Every 10 seconds (default)
- **Retention**: According to Grafana Cloud plan

### Data in Grafana Cloud
- Profiles appear within 15-30 seconds
- Historical comparison available
- Flame graphs for visualization
- Diff view to compare time periods

---

## 🚀 Deployment Timeline

| Time | Event | Action |
|------|-------|--------|
| T+0 | PR #65 merged | CI/CD starts building |
| T+5min | Build complete | Image pushed to Docker Hub |
| T+7min | Deploy starts | Kubernetes rollout begins |
| T+10min | **Deploy complete** | **Pods running with profiler** |
| T+12min | Add Pyroscope token | Run setup script |
| T+15min | Restart pods | Profiler activates |
| T+16min | Profiles flowing | View in Grafana Cloud |

**Current**: T+2min (build in progress)

---

## ✅ Post-Deployment Checklist

After CI/CD completes:

- [ ] Verify new version deployed
- [ ] Add Pyroscope token to secret
- [ ] Restart pods to pick up token
- [ ] Check logs for "Pyroscope continuous profiling enabled"
- [ ] View profiles in Grafana Cloud
- [ ] Create flame graph visualization
- [ ] Identify first optimization target

---

## 🎊 Success Criteria

You'll know it's working when:

1. ✅ Pod logs show "Pyroscope continuous profiling enabled"
2. ✅ No errors in profiler initialization
3. ✅ Profiles appear in Grafana Cloud within 1-2 minutes
4. ✅ Flame graphs show your application's function calls
5. ✅ Can query by service, environment, pod

---

## 📞 Quick Commands

```bash
# Check CI/CD status
gh run list --branch main --limit 1

# Verify deployment
kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine

# Check for profiler
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps -l app=petrosa-tradeengine | grep Pyroscope

# Add token
bash scripts/setup-pyroscope-token.sh "glc_your_token"

# Restart after adding token
kubectl --kubeconfig=k8s/kubeconfig.yaml rollout restart deployment/petrosa-tradeengine -n petrosa-apps
```

---

## 🏆 Complete Observability Stack

After this PR deploys, you'll have:

```
┌────────────────────────────────────┐
│      Grafana Cloud (sa-east-1)     │
│                                    │
│  ✅ Tempo (Traces)                │
│  ✅ Prometheus (Metrics)          │
│  ✅ Loki (Logs)                   │
│  🔄 Pyroscope (Profiles) ← NEW!   │
│                                    │
└────────────────────────────────────┘
```

**All Four Signals Operational!** 🎉

---

## 🎓 Next Steps

1. ⏳ **Wait** for CI/CD (~5 more minutes)
2. 🔑 **Get** Pyroscope token from Grafana Cloud
3. 🔐 **Add** token using setup script
4. 🔄 **Restart** pods to activate profiler
5. 📊 **View** profiles in Grafana Cloud
6. 🎯 **Optimize** based on flame graph insights

---

**You're almost there!** Just need to add the Pyroscope token and you'll have complete enterprise-grade observability! 🚀
