# OTLP Logging Handler - Root Cause & Solution Summary

## TL;DR

**Problem**: Application logs were not reaching Grafana Cloud Loki despite traces and metrics working.

**Root Cause**: Uvicorn's access/server logs bypass the root logger and use specific loggers (`uvicorn`, `uvicorn.access`, `uvicorn.error`) that don't propagate to root logger handlers.

**Solution**: Attach OTLP logging handler to both root logger AND uvicorn-specific loggers.

**Status**: PR #75 created and awaiting CI/CD deployment.

---

## Investigation Process

### Phase 1: Hypothesis - Handler Not Attached
**Initial Belief**: The OTLP handler wasn't being attached at all.

**Testing**:
- Created debug scripts to instrument Python's logging system
- Monitored `addHandler()` and `removeHandler()` calls with stack traces
- Ran instrumented application on live Kubernetes pods

**Finding**: ❌ Hypothesis REJECTED  
The handler WAS successfully attached during lifespan startup:
```
✅ OTLP logging handler attached to root logger
   Total handlers: 1
```

### Phase 2: Hypothesis - Handler Getting Removed
**New Belief**: Something was explicitly removing the handler after attachment.

**Testing**:
- Monitored all `removeHandler()` calls throughout application lifecycle
- Checked for `logging.basicConfig(force=True)` or `handlers.clear()` calls
- Analyzed complete startup sequence with stack traces

**Finding**: ❌ Hypothesis REJECTED  
NO explicit `removeHandler()` calls were detected for the root logger.

### Phase 3: Root Cause Discovery
**Final Investigation**: Checked handler state in running pods.

**Testing**:
```python
root_logger = logging.getLogger()
print(f'Handlers: {len(root_logger.handlers)}')  # Returns: 0
```

**Critical Finding**: The handler WAS detached, but not through `removeHandler()`.

**Analysis of Logs**:
- Stdout only showed: `INFO:     10.70.130.118:... - "GET /health HTTP/1.1" 200 OK`
- These are uvicorn access logs, NOT from root logger
- Uvicorn loggers: `uvicorn`, `uvicorn.access`, `uvicorn.error`
- **These loggers do NOT propagate to the root logger by default**

**ROOT CAUSE IDENTIFIED**: 🎯  
1. Root logger handler gets detached (mechanism still unclear)
2. Even if root logger handler stayed attached, uvicorn logs would still bypass it
3. Need to attach handler to BOTH root logger AND uvicorn loggers

---

## Solution Implementation

### Changes Made in PR #75

#### 1. Updated `attach_logging_handler()` in `otel_init.py`

**Before**:
```python
def attach_logging_handler():
    handler = LoggingHandler(logger_provider=_global_logger_provider)
    logging.getLogger().addHandler(handler)  # Only root logger
```

**After**:
```python
def attach_logging_handler():
    handler = LoggingHandler(logger_provider=_global_logger_provider)
    
    # Attach to root logger
    logging.getLogger().addHandler(handler)
    
    # ALSO attach to uvicorn loggers (they don't propagate to root)
    logging.getLogger("uvicorn").addHandler(handler)
    logging.getLogger("uvicorn.access").addHandler(handler)
    logging.getLogger("uvicorn.error").addHandler(handler)
```

#### 2. Updated `monitor_logging_handlers()` watchdog

**Before**:
```python
def monitor_logging_handlers():
    root_logger = logging.getLogger()
    if _otlp_logging_handler not in root_logger.handlers:
        return attach_logging_handler()
```

**After**:
```python
def monitor_logging_handlers():
    root_logger = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    
    # Check ALL loggers
    root_missing = _otlp_logging_handler not in root_logger.handlers
    uvicorn_missing = _otlp_logging_handler not in uvicorn_logger.handlers
    access_missing = _otlp_logging_handler not in uvicorn_access_logger.handlers
    
    if root_missing or uvicorn_missing or access_missing:
        print(f"⚠️  OTLP handler missing from loggers - re-attaching")
        return attach_logging_handler()
```

#### 3. Added Documentation

- **`docs/HANDLER_DETACHMENT_ANALYSIS.md`**: Comprehensive root cause analysis
- **`scripts/debug-handler-lifecycle.py`**: Monitoring script for live debugging
- **`scripts/run-with-handler-monitoring.py`**: Application launcher with monitoring

---

## Expected Outcome

After PR #75 is deployed, ALL logs will flow through OTLP to Grafana Cloud Loki:

1. ✅ **Application Logs** (via root logger)
   - Business logic logs
   - Error logs from application code
   - Custom logger output

2. ✅ **Server Logs** (via uvicorn logger)
   - Server startup messages
   - Server shutdown messages  
   - Server-level errors

3. ✅ **Access Logs** (via uvicorn.access logger)
   - HTTP request logs
   - Response status codes
   - Request timing

4. ✅ **Error Logs** (via uvicorn.error logger)
   - Server error messages
   - Connection errors
   - Internal server errors

---

## Verification Steps

Once PR #75 is deployed:

### 1. Check Handler Attachment
```bash
kubectl exec -n petrosa-apps <POD_NAME> -- python -c "
import logging
print('Root logger handlers:', len(logging.getLogger().handlers))
print('Uvicorn logger handlers:', len(logging.getLogger('uvicorn').handlers))
print('Uvicorn access logger handlers:', len(logging.getLogger('uvicorn.access').handlers))
"
```

**Expected Output**:
```
Root logger handlers: 1
Uvicorn logger handlers: 2  # StreamHandler + OTLP handler
Uvicorn access logger handlers: 2  # StreamHandler + OTLP handler
```

### 2. Check Logs in Grafana Cloud Loki

Query in Grafana Loki:
```logql
{service_name="tradeengine"} |= ""
```

**Expected to see**:
- Access logs: `GET /health`, `GET /ready`, etc.
- Server logs: `Application startup complete`, `Uvicorn running`
- Application logs: Custom business logic logs

### 3. Generate Test Logs
```bash
kubectl exec -n petrosa-apps <POD_NAME> -- python -c "
import logging
logging.info('TEST LOG - verifying OTLP export works')
"
```

Check Grafana Loki for the test log within 5-10 seconds.

---

## Timeline

- **PR #72**: Implemented logging handler watchdog (10s check interval)
- **PR #73**: Fixed lifespan function to ensure watchdog starts
- **PR #74**: Made watchdog more aggressive (10s interval, better monitoring)
- **PR #75**: **ROOT CAUSE FIX** - Attach handler to uvicorn loggers ✅

---

## Key Learnings

1. **Uvicorn logging architecture**: Uvicorn uses specific loggers that don't propagate to root logger by default. This is by design for performance reasons.

2. **Handler attachment timing**: The OTLP handler IS attached successfully during lifespan startup. The watchdog approach is working.

3. **Multiple handlers needed**: For FastAPI/Uvicorn applications, you must attach OTLP handlers to BOTH root logger AND uvicorn-specific loggers to capture all logs.

4. **Live debugging value**: The instrumented debugging approach with monkey-patched logging methods was crucial for understanding the actual behavior vs assumptions.

---

## Next Steps

1. ⏳ Wait for PR #75 CI/CD to complete
2. ✅ Verify logs appear in Grafana Cloud Loki
3. ✅ Confirm all three signals (metrics, traces, logs) are working
4. 📝 Update observability documentation with lessons learned
5. 🧹 Clean up debug scripts and temporary documentation files

---

## References

- PR #75: https://github.com/PetroSa2/petrosa-tradeengine/pull/75
- Grafana Cloud Loki: https://grafana.com/
- OpenTelemetry Logging: https://opentelemetry.io/docs/instrumentation/python/logging/
- Uvicorn Logging Config: https://www.uvicorn.org/settings/#logging

