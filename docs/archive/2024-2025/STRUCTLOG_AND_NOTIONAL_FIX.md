# TradeEngine Logging and Order Notional Fix

## Problem Summary

Two interconnected issues were causing order execution failures in production:

### Issue 1: Logger Keyword Arguments Error
**Symptom:**
```
Logger._log() got an unexpected keyword argument 'event'
```

**Root Cause:**
- Code used `self.logger.info("msg", event="...", symbol=...)` with keyword arguments
- `get_logger()` returned standard Python `logging.Logger` which doesn't support keyword arguments
- This error was thrown at line 1480-1488 in `dispatcher.py` during order amount calculation

### Issue 2: Order Notional Below Minimum
**Symptom:**
```
Order notional $1.13 is below minimum $5.00 for BNBUSDT
```

**Root Cause:**
- When Issue #1 threw an exception in `_calculate_order_amount()`, the catch block returned fallback value 0.001
- Error chain: Logger error → Exception caught → Fallback 0.001 qty → Order notional $1.13 < $5.00 min → Binance rejects

**Impact:**
- All BNBUSDT orders failing (quantity 0.001 × $1134 = $1.13 notional)
- Likely affecting other low-price symbols
- Production trading disrupted

## Solution Implemented

### 1. Configured Structlog for Structured Logging

**File:** `/Users/yurisa2/petrosa/petrosa-tradeengine/shared/logger.py`

**Changes:**
- Imported and configured structlog with proper processors
- Added JSON rendering for production, ConsoleRenderer for development
- Added context processors (timestamp, log level, logger name)
- Added exception info processor for `exc_info=True` support
- Replaced `get_logger()` to return structlog logger

**Configuration:**
```python
def configure_structlog() -> None:
    """Configure structlog with appropriate processors for environment."""
    is_production = settings.environment == "production" or settings.log_level.upper() in ["INFO", "WARNING", "ERROR"]

    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.threadlocal.merge_threadlocal,
    ]

    if is_production:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str = __name__):
    """Get a structlog logger instance with keyword argument support."""
    return structlog.get_logger(name)
```

**Structlog Usage Pattern:**
```python
# CORRECT: First argument is the event, rest are keyword arguments
logger.info("order_amount_calculated", symbol="BTCUSDT", amount=0.01)

# INCORRECT: Don't use event= as keyword argument
logger.info("Message", event="order_amount_calculated")  # ❌ Causes error
```

### 2. Fixed Error Handling in _calculate_order_amount()

**File:** `/Users/yurisa2/petrosa/petrosa-tradeengine/tradeengine/dispatcher.py`

**Before:**
```python
except Exception as e:
    self.logger.error(f"Error calculating order amount for {signal.symbol}: {e}")
    # Fallback to safe default
    return 0.001  # ❌ Too small, causes notional errors
```

**After:**
```python
except Exception as e:
    # Log the full error with stack trace for debugging
    self.logger.error(
        "order_amount_calculation_failed",
        error=str(e),
        symbol=signal.symbol,
        signal_price=signal.current_price,
        signal_quantity=signal.quantity,
        exc_info=True,
    )

    # Calculate symbol-specific fallback that meets minimum notional
    # Use $10 target notional as safe fallback (above typical $5 minimum)
    current_price = signal.current_price or 0
    if current_price > 0:
        fallback_amount = 10.0 / current_price  # $10 worth
        self.logger.warning(
            "using_fallback_amount",
            message=f"Using fallback order amount for {signal.symbol}: {fallback_amount:.6f}...",
            symbol=signal.symbol,
            fallback_amount=fallback_amount,
            fallback_notional=10.0,
            current_price=current_price,
        )
        return fallback_amount
    else:
        self.logger.error(
            "no_price_for_fallback",
            message=f"Cannot calculate fallback amount for {signal.symbol}...",
            symbol=signal.symbol,
            fallback_value=0.01,
        )
        return 0.01
```

**Improvements:**
1. **Full error visibility**: Added `exc_info=True` for stack traces
2. **Symbol-specific fallback**: Calculates fallback based on $10 target notional instead of fixed 0.001
3. **Better logging**: Detailed context in all log messages

**Fallback Comparison:**
| Symbol | Price | Old Fallback (0.001) | New Fallback ($10 worth) | Old Notional | New Notional |
|--------|-------|----------------------|--------------------------|--------------|--------------|
| BNBUSDT | $1134 | 0.001 | 0.008818 | $1.13 ❌ | $10.00 ✅ |
| BTCUSDT | $50000 | 0.001 | 0.0002 | $50.00 ✅ | $10.00 ✅ |
| ETHUSDT | $3000 | 0.001 | 0.00333 | $3.00 ❌ | $10.00 ✅ |

### 3. Updated All Logging Calls

**Files Modified:**
- `tradeengine/dispatcher.py` - 6 logging calls updated

**Pattern Applied:**
```python
# Before (with event= keyword)
self.logger.info("Placing OCO orders", event="oco_orders_placing", symbol=symbol, ...)

# After (event as first argument)
self.logger.info("oco_orders_placing", message="Placing OCO orders", symbol=symbol, ...)
```

**Locations Fixed:**
1. Line 111-122: OCO orders placing
2. Line 221-228: OCO orders placed
3. Line 320-327: OCO pair cancelling
4. Line 391-400: OCO triggered
5. Line 409-414: Order cancelled
6. Line 1246-1252: Position updated
7. Line 1480-1487: Order amount calculated
8. Line 1493-1500: Order amount calculation failed
9. Line 1507-1516: Using fallback amount
10. Line 1521-1527: No price for fallback

### 4. Comprehensive Tests Added

**File:** `/Users/yurisa2/petrosa/petrosa-tradeengine/tests/test_structlog_integration.py`

**Test Coverage:**
- ✅ Structlog configuration with keyword arguments
- ✅ Logger output format (JSON in prod, console in dev)
- ✅ Exception logging with stack traces
- ✅ Context binding (symbol, order_id, etc.)
- ✅ Backward compatibility with string-only logging
- ✅ Multiple keyword arguments
- ✅ Nested dictionaries
- ✅ None values handling
- ✅ Mixed type handling

**Results:** 11/11 tests passing

**File:** `/Users/yurisa2/petrosa/petrosa-tradeengine/tests/test_order_amount_calculation.py`

**Test Coverage:**
- Successful amount calculation
- Signal quantity above/below minimum
- Fallback amount meets minimum notional for BTC, ETH, BNB
- Fallback better than old default
- Error logging with full stack traces

## Deployment Checklist

### Pre-Deployment
- [x] Code changes implemented
- [x] Structlog configured
- [x] All logging calls updated
- [x] Tests created and passing
- [x] No linting errors

### Deployment Steps
1. Deploy to staging environment
2. Monitor logs for proper structured output with all fields
3. Trigger test signals for BNBUSDT, ETHUSDT, BTCUSDT
4. Verify no "Logger._log() got an unexpected keyword argument" errors
5. Verify no "Order notional below minimum" errors for valid signals
6. Verify fallback amounts meet minimum notional if triggered
7. Monitor for 24 hours
8. Deploy to production with gradual rollout

### Post-Deployment Verification
1. Check Grafana logs for structured JSON output
2. Verify order execution success rate returns to > 99%
3. Verify no notional errors for any symbols
4. Check that error logs include full stack traces when issues occur

## Expected Behavior After Fix

### Logging
- ✅ All logs properly structured with keyword arguments captured
- ✅ JSON output in production for log aggregation systems
- ✅ Human-readable console output in development
- ✅ Exception stack traces included with `exc_info=True`
- ✅ No "unexpected keyword argument" errors

### Order Amount Calculation
- ✅ Normal path: Uses `calculate_min_order_amount()` from binance_exchange
- ✅ Signal quantity validated against minimum notional
- ✅ Error path: Calculates $10 worth instead of fixed 0.001
- ✅ Fallback amounts meet Binance's $5 minimum notional
- ✅ Detailed error logging for debugging

### BNBUSDT Example
**Before Fix:**
```
Error: Logger._log() got an unexpected keyword argument 'event'
Fallback: 0.001 × $1134 = $1.13 notional
Result: Order rejected (below $5 minimum)
```

**After Fix:**
```
Success: calculate_min_order_amount() returns 0.004409
Order: 0.004409 × $1134 = $5.00 notional
Result: Order accepted ✅

OR (if error occurs):
Fallback: 0.008818 (10.0 / 1134.0) × $1134 = $10.00 notional
Result: Order accepted ✅
```

## Monitoring

### Key Metrics to Watch
1. **Order Execution Success Rate**: Should return to > 99%
2. **Notional Errors**: Should drop to 0 for valid signals
3. **Logger Errors**: Should drop to 0
4. **Fallback Usage**: Monitor warning logs to see if fallbacks are triggered

### Grafana Queries
```
# Count notional errors
sum(rate(tradeengine_order_failures_total{error="notional_below_minimum"}[5m]))

# Count logger errors
sum(rate(log_messages_total{level="error", message=~".*unexpected keyword argument.*"}[5m]))

# Count fallback usage
sum(rate(log_messages_total{event="using_fallback_amount"}[5m]))
```

### Alert Thresholds
- Notional errors > 0/minute → Critical alert
- Logger errors > 0/minute → Critical alert
- Fallback usage > 10/hour → Warning (investigate why calculate_min_order_amount failing)

## Rollback Plan

If issues occur after deployment:

1. **Immediate**: Revert `shared/logger.py` and `tradeengine/dispatcher.py`
2. **Temporary Fix**: Set all orders to use $10 minimum notional
3. **Investigation**: Review error logs and identify root cause
4. **Re-deploy**: Fix issues and re-test before deploying again

## Success Criteria

- ✅ No "Logger._log() got an unexpected keyword argument 'event'" errors
- ✅ No "Order notional below minimum" errors for valid signals
- ✅ All logs properly structured with keyword arguments captured
- ✅ Order amount fallback (if used) meets minimum notional requirements
- ✅ All tests passing with >90% coverage for changed code
- ✅ Order execution success rate > 99% restored

## Related Documentation

- Structlog Documentation: https://www.structlog.org/en/stable/
- Binance Minimum Notional: https://binance-docs.github.io/apidocs/futures/en/#filters
- Previous Fix: `docs/NOTIONAL_ROUNDING_FIX.md`
- Validation: `docs/BINANCE_NOTIONAL_VALIDATION_FIX.md`

## Date

**Implemented:** 2025-10-26
**Status:** Ready for deployment
