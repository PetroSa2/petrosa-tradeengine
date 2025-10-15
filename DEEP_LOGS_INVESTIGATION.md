# Deep Investigation: Why Logs Still Not Appearing

## 🎯 Systematic Investigation Plan

### Phase 1: Verify PR #68 Actually Deployed

```bash
# 1. Check current image version
kubectl --kubeconfig=k8s/kubeconfig.yaml get deployment petrosa-tradeengine -n petrosa-apps \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# Expected: v1.1.54 or v1.1.55 (newer than v1.1.53)

# 2. Check pod creation time (should be recent)
kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine \
  -o custom-columns=NAME:.metadata.name,AGE:.metadata.creationTimestamp

# Expected: Pods created in last 30 minutes

# 3. Check CI/CD deployment log
gh run list --branch main --workflow="CI/CD Pipeline" --limit 1
```

### Phase 2: Verify Logging Handler is Attached

```bash
# Get a pod name
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')

# Test 1: Check number of handlers
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- python -c "
import logging
root = logging.getLogger()
print(f'Root logger handlers: {len(root.handlers)}')
for i, h in enumerate(root.handlers):
    print(f'  Handler {i+1}: {type(h).__name__}')
"

# Expected: At least 1 handler, type should include 'LoggingHandler'
# If 0 handlers: PR #68 not deployed yet OR handler attachment failed

# Test 2: Check pod startup logs
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n petrosa-apps $POD | grep -E "OTLP logging|attach"

# Expected:
# ✅ OpenTelemetry logging export configured for tradeengine
# ✅ OTLP logging handler attached to root logger
#    Total handlers: 1
```

### Phase 3: Test OTLP Export Manually

```bash
# Run test inside pod to verify OTLP pipeline
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -it -n petrosa-apps $POD -- python << 'EOF'
import logging
import time

# Check if handler exists
root = logging.getLogger()
print(f"Handlers: {len(root.handlers)}")

if len(root.handlers) == 0:
    print("ERROR: No handlers attached!")
    exit(1)

# Send test logs
logger = logging.getLogger("manual_test")
logger.info("TEST LOG 1: Manual test from kubectl exec")
logger.warning("TEST LOG 2: Warning level test")
logger.error("TEST LOG 3: Error level test")

print("Sent 3 test logs. Wait 10 seconds for batch export...")
time.sleep(10)
print("Check Grafana Cloud Loki for these test logs")
EOF
```

### Phase 4: Check Grafana Alloy Reception

```bash
# Watch Alloy logs in real-time while sending logs
# Terminal 1: Watch Alloy
kubectl --kubeconfig=k8s/kubeconfig.yaml logs -n observability -l app=grafana-alloy -f

# Terminal 2: Send logs from tradeengine
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- wget -q -O- http://localhost:8000/health
```

Look for:
- Log reception messages
- Batch processing
- Export activity
- Any errors

### Phase 5: Check OTLP Receiver Health

```bash
# Get Alloy pod
ALLOY_POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n observability -l app=grafana-alloy -o jsonpath='{.items[0].metadata.name}')

# Check if OTLP receiver is listening
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n observability $ALLOY_POD -- netstat -ln | grep 4317

# Expected: Shows :4317 in LISTEN state

# Check Alloy component status
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n observability $ALLOY_POD -- wget -q -O- http://localhost:12345/api/v0/web/components | jq '.components[] | select(.name | contains("otlp"))'

# Expected: Shows OTLP components with "running" health status
```

### Phase 6: Network Trace

```bash
# From tradeengine pod to Alloy
POD=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}')

# Test TCP connection
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- nc -zv grafana-alloy.observability.svc.cluster.local 4317

# Test with actual gRPC call (if grpcurl available)
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD -- sh -c "
python3 -c '
import socket
import sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect((\"grafana-alloy.observability.svc.cluster.local\", 4317))
    print(\"✅ TCP connection successful\")
    s.close()
except Exception as e:
    print(f\"❌ Connection failed: {e}\")
    sys.exit(1)
'
"
```

### Phase 7: Enable Debug Logging

If still no logs, enable debug mode:

```python
# In otel_init.py, temporarily add:
import logging
logging.basicConfig(level=logging.DEBUG)

# Or set environment variable:
# OTEL_LOG_LEVEL=debug
```

This will show what OTLP SDK is actually doing.

## 🔍 Potential Issues to Check

### Issue 1: Handler Not Actually Attached
**Symptom**: `len(logging.getLogger().handlers) == 0`
**Cause**: PR #68 not deployed OR attach_logging_handler() not called
**Fix**: Verify deployment, check pod logs for "attached" message

### Issue 2: Handler Attached to Wrong Logger
**Symptom**: Handler exists but logs still not exported
**Cause**: Attaching to root logger but app uses named loggers
**Fix**: Attach to specific loggers or ensure propagation enabled

### Issue 3: Log Level Filtering
**Symptom**: Some logs exported, not all
**Cause**: Handler level set too high (e.g., WARNING when logs are INFO)
**Fix**: Verify handler level is NOTSET or INFO

### Issue 4: Batch Not Flushing
**Symptom**: Logs generated but never exported
**Cause**: Batch processor not flushing (waiting for more logs)
**Fix**: Force flush or lower batch size

### Issue 5: Silent OTLP Export Failure
**Symptom**: No errors but logs don't reach Grafana Cloud
**Cause**: OTLP exporter failing silently
**Fix**: Enable OTLP debug logging, check for exceptions

### Issue 6: Wrong Loki Datasource
**Symptom**: Logs exported but not visible in UI
**Cause**: OTLP logs go to different datasource than Kubernetes logs
**Fix**: Check ALL Loki datasources in Grafana Cloud

### Issue 7: Label Mismatch
**Symptom**: Logs in Loki but query doesn't find them
**Cause**: OTLP logs have different labels than expected
**Fix**: Try broader queries: `{service_name="tradeengine"}` or `{}`

## 🧪 Definitive Test

Create a minimal test that MUST work if pipeline is correct:

```python
# Run inside tradeengine pod
import logging
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource

# Create everything fresh
resource = Resource.create({"service.name": "direct-test", "test.id": "123"})
exporter = OTLPLogExporter(
    endpoint="grafana-alloy.observability.svc.cluster.local:4317",
    insecure=True
)
provider = LoggerProvider(resource=resource)
provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
handler = LoggingHandler(level=logging.INFO, logger_provider=provider)

# Attach to a fresh logger
test_logger = logging.getLogger("definitive_test")
test_logger.addHandler(handler)
test_logger.setLevel(logging.INFO)

# Send test log
test_logger.info("DEFINITIVE TEST LOG - If you see this in Loki, OTLP works!")

# Force export
import time
time.sleep(2)
provider.force_flush()
time.sleep(5)

print("Test log sent. Check Loki for service_name='direct-test'")
```

If this works → Problem is in our handler attachment
If this fails → Problem is in OTLP pipeline itself

## 📊 Investigation Checklist

After running all tests above, we'll know:

- [ ] Is PR #68 actually deployed?
- [ ] Is handler attached to root logger?
- [ ] Are logs being generated with proper formatting?
- [ ] Is OTLP exporter being called?
- [ ] Is Grafana Alloy receiving OTLP logs?
- [ ] Is Grafana Alloy forwarding to Grafana Cloud?
- [ ] Are logs reaching Loki but with different labels?
- [ ] Is there a silent authentication failure?

---

**Run these tests systematically to pinpoint the exact failure point.**
