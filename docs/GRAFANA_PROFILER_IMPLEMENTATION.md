# Grafana Cloud Profiler (Pyroscope) Implementation Guide

## 🔥 What is Grafana Cloud Profiler?

**Grafana Cloud Profiler** (powered by Pyroscope) provides **continuous profiling** for your applications:

- **CPU Profiling**: Identifies which functions consume the most CPU time
- **Memory Profiling**: Tracks memory allocation and leaks
- **Flame Graphs**: Visual representation of your application's performance
- **Historical Comparison**: Compare profiles over time to track performance regressions
- **Always-On**: Continuous profiling with minimal overhead (~1-5%)

### Why Use It?

- 🐌 **Find Performance Bottlenecks**: See exactly which code is slow
- 💾 **Detect Memory Leaks**: Track memory allocations over time
- 📊 **Optimize Resource Usage**: Reduce CPU/memory consumption
- 🔍 **Production Debugging**: Profile production workloads safely
- 📈 **Track Performance Over Time**: See if code changes improve/degrade performance

---

## 🎯 Implementation Options for Python/FastAPI

### Option 1: Pyroscope Python SDK (Recommended for Application-Level Profiling)

**Best for**: Application-level profiling with minimal overhead

**Pros**:
- Easy to implement
- Low overhead (~2-5%)
- Works with any Python app
- Rich profiling data

**Implementation**:

#### 1. Add Pyroscope SDK to requirements

```bash
# Add to requirements.txt
pyroscope-io>=0.8.0
```

#### 2. Update `otel_init.py` or create `profiler_init.py`

```python
import os
import pyroscope

def setup_profiler(service_name: str = "tradeengine"):
    """Set up Pyroscope continuous profiling"""

    if os.getenv("ENABLE_PROFILER", "false").lower() not in ("true", "1", "yes"):
        print("⚠️  Profiling disabled")
        return

    # Get Grafana Cloud Pyroscope endpoint
    profiler_url = os.getenv(
        "PYROSCOPE_SERVER_ADDRESS",
        "https://profiles-prod-011.grafana.net"
    )

    # Get authentication
    auth_token = os.getenv("PYROSCOPE_AUTH_TOKEN", "")

    if not auth_token:
        print("⚠️  PYROSCOPE_AUTH_TOKEN not set, profiling disabled")
        return

    try:
        pyroscope.configure(
            application_name=service_name,
            server_address=profiler_url,
            auth_token=auth_token,
            # Optional: Add tags for better filtering
            tags={
                "environment": os.getenv("ENVIRONMENT", "production"),
                "hostname": os.getenv("HOSTNAME", "unknown"),
                "version": os.getenv("OTEL_SERVICE_VERSION", "unknown"),
            },
            # Profiling options
            detect_subprocesses=True,
            oncpu=True,  # CPU profiling
            native=False,  # Set to True for C extensions profiling
            gil_only=True,  # Python-specific: profile only with GIL held
        )
        print(f"✅ Pyroscope profiling enabled for {service_name}")
        print(f"   Server: {profiler_url}")

    except Exception as e:
        print(f"⚠️  Failed to initialize Pyroscope profiling: {e}")
```

#### 3. Initialize in `api.py`

```python
# In api.py, after importing otel_init
import profiler_init

# At module level (after otel_init.setup_telemetry())
profiler_init.setup_profiler("tradeengine")
```

#### 4. Add Environment Variables to Deployment

```yaml
# k8s/deployment.yaml
env:
- name: ENABLE_PROFILER
  value: "true"
- name: PYROSCOPE_SERVER_ADDRESS
  value: "https://profiles-prod-011.grafana.net"  # Your Grafana Cloud region
- name: PYROSCOPE_AUTH_TOKEN
  valueFrom:
    secretKeyRef:
      name: petrosa-sensitive-credentials
      key: PYROSCOPE_AUTH_TOKEN
```

---

### Option 2: eBPF-Based Profiling (No Code Changes Required)

**Best for**: Infrastructure-level profiling without modifying code

**Pros**:
- Zero code changes
- Works for any language (Python, Go, C++, etc.)
- Lower overhead
- System-wide profiling

**Implementation**:

Deploy Grafana Alloy with eBPF profiling enabled:

```alloy
// In Grafana Alloy config
pyroscope.ebpf "instance" {
  forward_to = [pyroscope.write.grafana_cloud.receiver]

  targets = discovery.kubernetes.pods.targets

  // Only profile petrosa-apps
  relabel_configs = [{
    source_labels = ["__meta_kubernetes_namespace"]
    regex         = "petrosa-apps"
    action        = "keep"
  }]
}

pyroscope.write "grafana_cloud" {
  endpoint {
    url = "https://profiles-prod-011.grafana.net"
    basic_auth {
      username = "YOUR_USER_ID"
      password = "YOUR_PYROSCOPE_TOKEN"
    }
  }
}
```

---

### Option 3: OTLP Profiling (Experimental - Future)

**Status**: Emerging standard, not yet widely supported

OpenTelemetry is working on a profiling signal that would allow sending profiles via OTLP (like traces/metrics/logs). This is not yet production-ready.

---

## 🚀 Quick Start: Pyroscope Python SDK

### Step 1: Get Your Grafana Cloud Pyroscope Credentials

```bash
# Find your Pyroscope endpoint in Grafana Cloud
# Go to: https://grafana.com/orgs/yurisa2/stacks
# Look for "Profiles" section
# You'll need:
# - Endpoint URL (e.g., https://profiles-prod-011.grafana.net)
# - User ID
# - API Token (with profiles:write permission)
```

### Step 2: Add Pyroscope to TradeEngine

```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine

# Add to requirements.txt
echo "pyroscope-io>=0.8.0" >> requirements.txt

# Create profiler initialization file
cat > profiler_init.py << 'EOF'
import os
import pyroscope

def setup_profiler():
    """Initialize Pyroscope continuous profiling"""
    if os.getenv("ENABLE_PROFILER", "false").lower() not in ("true", "1", "yes"):
        return

    try:
        pyroscope.configure(
            application_name="tradeengine",
            server_address=os.getenv("PYROSCOPE_SERVER_ADDRESS", ""),
            auth_token=os.getenv("PYROSCOPE_AUTH_TOKEN", ""),
            tags={
                "environment": os.getenv("ENVIRONMENT", "production"),
                "pod": os.getenv("HOSTNAME", "unknown"),
            },
        )
        print("✅ Pyroscope profiling enabled")
    except Exception as e:
        print(f"⚠️  Profiling setup failed: {e}")

# Auto-initialize if enabled
if os.getenv("ENABLE_PROFILER", "false").lower() in ("true", "1", "yes"):
    setup_profiler()
EOF
```

### Step 3: Import in api.py

```python
# Add after: import otel_init
import profiler_init  # Will auto-initialize if ENABLE_PROFILER=true
```

### Step 4: Update Kubernetes Secret

```bash
# Add Pyroscope token to petrosa-sensitive-credentials
kubectl --kubeconfig=k8s/kubeconfig.yaml create secret generic \
  petrosa-sensitive-credentials \
  --from-literal=PYROSCOPE_AUTH_TOKEN="<your-pyroscope-token>" \
  --dry-run=client -o yaml | kubectl --kubeconfig=k8s/kubeconfig.yaml apply -f -
```

### Step 5: Update deployment.yaml

```yaml
env:
- name: ENABLE_PROFILER
  value: "true"
- name: PYROSCOPE_SERVER_ADDRESS
  value: "https://profiles-prod-XXX.grafana.net"  # Your region
- name: PYROSCOPE_AUTH_TOKEN
  valueFrom:
    secretKeyRef:
      name: petrosa-sensitive-credentials
      key: PYROSCOPE_AUTH_TOKEN
```

### Step 6: Deploy via CI/CD

```bash
git checkout -b feat/add-continuous-profiling
git add profiler_init.py requirements.txt k8s/deployment.yaml
git commit -m "feat: add Pyroscope continuous profiling"
git push origin feat/add-continuous-profiling
gh pr create --title "feat: add continuous profiling with Pyroscope"
# Wait for CI/CD, then merge
```

---

## 📊 What You'll See in Grafana Cloud

### Flame Graphs
- **X-axis**: Alphabetical ordering of functions
- **Y-axis**: Stack depth (call hierarchy)
- **Width**: Proportion of CPU time spent
- **Color**: Different colors for different code paths

### Profile Types Available

1. **CPU Profile**: Which functions use most CPU time
2. **Allocated Memory**: Memory allocation patterns
3. **In-Use Memory**: Current memory usage
4. **Goroutines**: (For Go apps, not applicable to Python)
5. **Block**: Blocking operations (locks, I/O)

### Example Queries

```
# All profiles for tradeengine
{service_name="tradeengine"}

# Profile by environment
{service_name="tradeengine", environment="production"}

# Compare two time periods
# Select time range, then click "Compare" to diff profiles
```

---

## 🎓 Understanding Profiling Data

### Reading a Flame Graph

```
Wide bars = Functions that consume lots of CPU/memory
Tall stacks = Deep call chains (potential optimization targets)
Flat graphs = Good distribution (no single bottleneck)
```

### Common Insights

- **Wide bar in business logic**: Optimization opportunity
- **Wide bar in I/O operations**: Consider caching or async
- **Memory growth**: Potential memory leak
- **Deep recursion**: May need algorithm optimization

---

## 🔧 Advanced Configuration

### Sampling Rate

```python
pyroscope.configure(
    # ... other config ...
    sample_rate=100,  # Profile every 100th operation (default: 100Hz)
)
```

### Profile Types

```python
pyroscope.configure(
    # ... other config ...
    oncpu=True,           # CPU profiling
    allocation=True,      # Memory allocation profiling
    blocking=True,        # Lock/blocking profiling
)
```

### Custom Tags for Filtering

```python
pyroscope.configure(
    # ... other config ...
    tags={
        "environment": os.getenv("ENVIRONMENT"),
        "region": "sa-east-1",
        "service": "tradeengine",
        "pod": os.getenv("HOSTNAME"),
        "version": os.getenv("OTEL_SERVICE_VERSION"),
    },
)
```

---

## 🎯 Use Cases for TradeEngine

### 1. Find Slow Order Processing
**Query**: Profile during high trade volume
**Look for**: Wide bars in dispatcher, exchange, or position_manager functions

### 2. Optimize Database Queries
**Query**: Profile MongoDB operations
**Look for**: Time spent in MongoDB driver calls

### 3. Identify Memory Leaks
**Query**: Compare memory profiles over 24 hours
**Look for**: Steady memory growth without corresponding load increase

### 4. Optimize NATS Message Processing
**Query**: Profile during message bursts
**Look for**: Time in NATS consumer and signal processing

---

## 📈 Monitoring Profiler Health

### Check if Profiling is Active

```bash
# In pod logs
kubectl logs -n petrosa-apps -l app=petrosa-tradeengine | grep -i pyroscope

# Expected:
# ✅ Pyroscope profiling enabled
```

### View in Grafana Cloud

1. Go to https://yurisa2.grafana.net
2. Navigate to **Explore**
3. Select **Pyroscope** datasource
4. Query: `{service_name="tradeengine"}`
5. Select profile type: CPU, Memory, etc.

---

## ⚠️ Important Considerations

### Performance Impact
- **CPU Overhead**: 1-5% typically
- **Memory Overhead**: ~10-50MB per process
- **Network**: ~100-500KB/minute profile data

### Production Safety
- Start with sampling (every 100th event)
- Monitor overhead in production
- Can be enabled/disabled with environment variable
- Safe to run continuously

### Privacy
- Profiles don't contain sensitive data
- Only function names and call stacks
- No variable values or user data

---

## 🔗 Documentation Links

- **Pyroscope Docs**: https://grafana.com/docs/pyroscope/
- **Python SDK**: https://pyroscope.io/docs/python/
- **Grafana Alloy Profiling**: https://grafana.com/docs/alloy/latest/reference/components/pyroscope.ebpf/

---

## 🎊 Complete Observability Stack

After adding profiling, you'll have:

```
┌─────────────────────────────────────────┐
│         Grafana Cloud Platform          │
│                                         │
│  ✅ Tempo (Traces)                     │
│  ✅ Prometheus (Metrics)               │
│  ✅ Loki (Logs)                        │
│  ⏳ Pyroscope (Profiles)  ← Add this!  │
│                                         │
└─────────────────────────────────────────┘
```

**The Four Pillars of Observability**:
1. ✅ Traces - What happened and when
2. ✅ Metrics - How much and how fast
3. ✅ Logs - Detailed event information
4. ⏳ Profiles - Why it's slow (performance analysis)

---

## 📝 Next Steps

1. **Get Pyroscope credentials** from Grafana Cloud
2. **Decide on implementation**: SDK (code changes) vs eBPF (no code changes)
3. **Add configuration** to tradeengine
4. **Deploy** via CI/CD workflow
5. **Verify** profiles appear in Grafana Cloud
6. **Analyze** performance and optimize

---

**Ready to implement?** Let me know which approach you prefer (SDK or eBPF) and I'll help you set it up!
