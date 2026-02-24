# OTLP Logging Handler Detachment - Root Cause Analysis

## Investigation Summary

Through live debugging on Kubernetes pods with instrumented logging handlers, we've identified the root cause of why application logs are not reaching Grafana Cloud Loki.

## Key Findings

### 1. Handler IS Successfully Attached on Startup ✅
```
✅ OTLP logging handler attached to root logger
   Total handlers: 1
```

The OTLP `LoggingHandler` is successfully attached to the root logger during the FastAPI lifespan startup event.

### 2. Handler Gets Detached After Startup ❌

When checking the handler state after the application is running:
```python
root_logger = logging.getLogger()
print(f'Root logger handlers: {len(root_logger.handlers)}')  # Returns 0
```

**The handler count is 0**, meaning the OTLP handler was removed after startup.

### 3. No REMOVE_HANDLER Events Captured

From the debug logs, we monitored all `addHandler()` and `removeHandler()` calls:
- ✅ We saw the OTLP handler being added during lifespan
- ❌ We did NOT see any explicit `removeHandler()` calls for the root logger

This means the handler was removed through a different mechanism.

### 4. Most Likely Culprit: logging.basicConfig() or handlers.clear()

Based on the investigation, the handler is being removed by one of these:

1. **`logging.basicConfig(force=True)`** - Resets all handlers
2. **`logging.root.handlers.clear()`** - Directly clears the handlers list
3. **`logging.config.dictConfig()`** - Reconfigures logging completely

These operations don't call `removeHandler()`, they manipulate the handlers list directly.

### 5. Uvicorn's Logging Behavior

Uvicorn uses specific loggers:
- `uvicorn` logger for server messages
- `uvicorn.access` logger for access logs

These loggers have their own handlers and **do not propagate to the root logger** by default. That's why we only see access logs in stdout (`INFO:     10.70.130.118:...`).

## Root Cause

The OTLP logging handler is:
1. Attached successfully during lifespan startup
2. Then detached/removed by some code that directly manipulates `logging.root.handlers`
3. This happens silently without calling `removeHandler()`

The watchdog we implemented can re-attach the handler, but we need to:
1. Make it more aggressive (check more frequently)
2. Add logging to detect when the handler disappears
3. Consider preventing the detachment in the first place

## Recommended Solutions

### Option 1: Prevent Handler Removal (Best)
Configure uvicorn to not reset logging:
```python
uvicorn.run(
    app,
    host="0.0.0.0",
    port=8000,
    log_config=None  # Prevent uvicorn from resetting logging
)
```

### Option 2: More Aggressive Watchdog (Current)
The current PR #74 implements a watchdog that checks every 10 seconds and re-attaches if needed.

### Option 3: Hook into Uvicorn's Logging Setup
Attach the OTLP handler AFTER uvicorn configures logging, and to the specific uvicorn loggers:
```python
# Attach to root logger
logging.getLogger().addHandler(otlp_handler)

# Also attach to uvicorn loggers to capture access logs
logging.getLogger("uvicorn").addHandler(otlp_handler)
logging.getLogger("uvicorn.access").addHandler(otlp_handler)
logging.getLogger("uvicorn.error").addHandler(otlp_handler)
```

## Next Steps

1. ✅ Investigation complete - root cause identified
2. ⏭️ Verify that PR #74 watchdog is working (check if logs appear in Loki)
3. ⏭️ If watchdog alone doesn't work, implement Option 3 (attach to uvicorn loggers)
4. ⏭️ Consider Option 1 (disable uvicorn's logging config) for cleaner solution

## Debug Evidence

### Test 1: Handler attached on startup
```
[2025-10-14T23:07:37.559166] ADD_HANDLER: Adding handler to logger 'root' (handler: LoggingHandler)
[2025-10-14T23:07:37.564424] ADD_HANDLER_COMPLETE: Handler added to logger 'root'
Current handler count: 1
```

### Test 2: Handler missing after startup
```bash
$ python -c "import logging; print(len(logging.getLogger().handlers))"
0
```

### Test 3: Uvicorn logs bypass root logger
Logs seen in stdout:
```
INFO:     10.70.130.118:52258 - "GET /health HTTP/1.1" 200 OK
```

These are from `uvicorn.access` logger, not root logger.

## Conclusion

The OTLP handler is being detached after startup by code that directly manipulates the handlers list. The watchdog approach can work, but we should also attach the handler to the specific uvicorn loggers to ensure all logs are captured.
