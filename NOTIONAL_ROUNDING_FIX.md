# Notional Rounding Fix - October 16, 2025

## Problem

Orders were failing with notional errors despite having notional validation:

```
Order notional $19.59 is below minimum $20.00 for ETHUSDT.
Need quantity >= 0.005103 at $3918.96.
Current quantity: 0.005000.
```

## Root Cause

The `calculate_min_order_amount()` function was using Python's `round()` function which can round DOWN, causing calculated quantities to fall below the minimum notional requirement.

### Example Issue
- Min notional: $20.00
- Price: $3918.96
- Required quantity: 20.00 / 3918.96 = 0.005103
- After 5% margin: 0.005103 × 1.05 = 0.005358
- After `round(0.005358, 3)`: **0.005** ❌
- Resulting notional: 0.005 × 3918.96 = **$19.59** ❌

## Solution

Changed the rounding logic to use `math.ceil()` to always round UP to the next valid step_size increment, ensuring orders always meet the minimum notional requirement.

### Fix Implementation

**File:** `tradeengine/exchange/binance.py`

1. **Added import:**
```python
import math
```

2. **Updated `calculate_min_order_amount()` method:**
```python
# Round UP to the next valid step_size increment
# This ensures we always meet the minimum notional requirement
if step_size > 0:
    # Calculate how many steps we need
    steps = math.ceil(final_min_qty / step_size)
    final_min_qty = steps * step_size

    # Round to appropriate precision to avoid floating point errors
    precision = min_info["precision"]
    final_min_qty = round(final_min_qty, precision)

# Verify the final quantity meets the minimum notional
# If not, add one more step_size increment
if current_price * final_min_qty < min_notional:
    final_min_qty += step_size
    precision = min_info["precision"]
    final_min_qty = round(final_min_qty, precision)
```

### After Fix
- Min notional: $20.00
- Price: $3918.96
- Required quantity: 20.00 / 3918.96 = 0.005103
- After 5% margin: 0.005103 × 1.05 = 0.005358
- After `math.ceil(0.005358 / 0.001) × 0.001`: **0.006** ✅
- Resulting notional: 0.006 × 3918.96 = **$23.51** ✅

## Test Results

Created comprehensive test suite in `tests/test_ethusdt_notional_fix.py`:

✅ **All 20 tests passed** (15 existing + 5 new ETHUSDT-specific tests)

### ETHUSDT Test Results
```
✓ ETHUSDT at $3918.96: qty=0.006000, notional=$23.51
✓ ETHUSDT at $3921.92: qty=0.006000, notional=$23.53
✓ Step size rounding validated
✓ Safety margin verified
```

## Impact

### Before
- **Failed orders:** Orders with quantities like 0.005000 ETH
- **Notional values:** $19.59 (below $20.00 minimum)
- **Error rate:** Multiple failures per minute

### After
- **Successful orders:** Quantities rounded up to 0.006000 ETH
- **Notional values:** $23.51 (safely above $20.00 minimum)
- **Error rate:** Expected to be zero for this issue

## Files Modified

1. `tradeengine/exchange/binance.py`
   - Added `import math`
   - Updated `calculate_min_order_amount()` method

2. `tests/test_ethusdt_notional_fix.py` (NEW)
   - 5 comprehensive tests for ETHUSDT scenarios
   - Tests exact error scenarios from logs
   - Tests various price points

## Deployment

1. ✅ Code changes implemented
2. ✅ All tests passing (20/20)
3. ✅ Linting passed
4. ⏳ Build Docker image
5. ⏳ Deploy to production
6. ⏳ Verify no more notional errors

## Monitoring

After deployment, monitor for:
- ✅ No more "Order notional below minimum" errors
- ✅ All ETHUSDT orders meet $20.00 minimum
- ✅ Order execution success rate increases
- ⚠️ Slightly higher order sizes (expected due to rounding up)

## Related Documentation

- `docs/BINANCE_NOTIONAL_VALIDATION_FIX.md` - Original MIN_NOTIONAL fix
- `tests/test_notional_validation.py` - Comprehensive validation tests
- `tests/test_ethusdt_notional_fix.py` - ETHUSDT-specific tests

## Authors

- AI Assistant (Cursor)
- Date: October 16, 2025
- Issue: Notional rounding causing order failures
