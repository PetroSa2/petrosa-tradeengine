# Trade Engine Bug Fixes - October 16, 2025

## Summary
Fixed multiple critical bugs affecting OCO orders, position tracking, datetime parsing, and MySQL persistence.

## Bugs Fixed

### 1. OCO Orders Receiving 0.0 Amount ❌ → ✅
**Issue:** Stop loss and take profit orders were being created with 0.0 amount, causing "Order amount must be positive" errors.

**Root Cause:** Market orders return `amount: 0` in the result when status is "NEW" (order placed but not yet filled). The OCO manager was using `result.get("amount")` which returned 0.

**Fix:**
- Added fallback logic in `dispatcher.py` to use `order.amount` when `result.get("amount")` is 0 or None
- Applied to:
  - `_place_risk_management_orders()` (line 1056-1059)
  - `_place_stop_loss_order()` (line 1127-1130)
  - `_place_take_profit_order()` (line 1218-1221)

**Files Modified:**
- `tradeengine/dispatcher.py`

---

### 2. String-Float Type Error in Fill Price ❌ → ✅
**Issue:** `unsupported operand type(s) for -: 'str' and 'float'` - fill_price was returned as string "0.00" instead of float.

**Root Cause:** Binance API can return numeric values as strings, and arithmetic operations fail when mixing string and float types.

**Fix:**
- Added type checking and conversion for `fill_price`, `amount`, and `commission` in position creation and updates
- Ensures all numeric values are converted to float before arithmetic operations

**Code Example:**
```python
# Ensure fill_price is a float, not a string
fill_price = result.get("fill_price", order.target_price or 0)
if isinstance(fill_price, str):
    fill_price = float(fill_price) if fill_price else (order.target_price or 0)
```

**Files Modified:**
- `tradeengine/position_manager.py` (lines 374-402, 277-285)

---

### 3. Invalid Datetime Parsing ('99') ❌ → ✅
**Issue:** `Invalid isoformat string: '99'` - NATS messages with invalid timestamp values caused parsing failures.

**Root Cause:** Signal timestamp validator was too strict and failed completely on invalid values, preventing the signal from being processed.

**Fix:**
- Enhanced timestamp validator to:
  - Validate Unix timestamp ranges (year 2000-2100)
  - Log warnings for invalid timestamps
  - Fallback to current time instead of raising errors
  - Gracefully handle edge cases

**Benefits:**
- Signals with invalid timestamps now process successfully with warnings
- System continues operating instead of crashing
- Better debugging with clear warning messages

**Files Modified:**
- `contracts/signal.py` (lines 186-238)

---

### 4. MySQL Retry Logic Missing ❌ → ✅
**Issue:** MySQL operations failed permanently on transient connection errors.

**Root Cause:** No retry logic for MySQL operations - single failures caused permanent data loss.

**Fix:**
- Added exponential backoff retry logic to critical MySQL operations:
  - `create_position()` - 3 retries with exponential backoff
  - `update_position_risk_orders()` - 3 retries with exponential backoff
- Configuration:
  - `MAX_RETRY_ATTEMPTS = 3`
  - `RETRY_DELAY = 1.0` seconds
  - `RETRY_BACKOFF_MULTIPLIER = 2.0`

**Retry Schedule:**
- Attempt 1: Immediate
- Attempt 2: After 1.0s
- Attempt 3: After 2.0s

**Files Modified:**
- `shared/mysql_client.py` (lines 108-193, 248-320)

---

### 5. Database Truth Value Testing Error ❌ → ✅
**Issue:** `Database objects do not implement truth value testing or bool(). Please compare with None instead: database is not None`

**Root Cause:** SQLAlchemy database objects were being used in boolean context (`if self.mongodb_db:`) instead of explicit None comparison.

**Fix:**
- Changed all database object checks from `if self.mongodb_db:` to `if self.mongodb_db is not None:`
- Applied to 3 locations in position_manager.py

**Files Modified:**
- `tradeengine/position_manager.py` (lines 413, 459, 536)

---

## Test Results

✅ All tests passing
✅ No linter errors
✅ Position tracking working correctly
✅ OCO orders being placed with correct amounts
✅ MySQL retry logic functioning
✅ Invalid timestamps handled gracefully

## Deployment Notes

No breaking changes - these are all bug fixes that improve reliability:

1. **OCO orders** - Now use correct quantities from original orders
2. **Type safety** - All numeric values properly typed
3. **Timestamp handling** - Gracefully handles invalid timestamps
4. **MySQL resilience** - Automatic retry on transient errors
5. **Database checks** - Proper None comparisons for SQLAlchemy objects

## Next Steps

1. ✅ Fix applied and tested
2. 🔄 Commit changes to feature branch
3. 🔄 Merge to main
4. 🔄 Deploy to production
5. Monitor logs for:
   - OCO order placement success
   - MySQL retry patterns
   - Invalid timestamp warnings
