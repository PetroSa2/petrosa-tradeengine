# Quick Reference: SL/TP Fix

**Issue**: Stop-loss and take-profit orders not appearing on Binance
**Status**: ✅ **FIXED**
**Date**: October 16, 2025

---

## The Problem

Binance Futures hedge mode rejects orders with explicit `reduceOnly=True` when `positionSide` is specified.

```
Error: APIError(code=-1106): Parameter 'reduceonly' sent when not required.
```

---

## The Solution

### In `binance.py` (for all order types):
```python
# OLD (BROKEN)
if order.reduce_only:
    params["reduceOnly"] = True  # ❌

# NEW (WORKING)
if order.position_side:
    params["positionSide"] = order.position_side
    # Binance auto-handles reduceOnly in hedge mode
else:
    if order.reduce_only:
        params["reduceOnly"] = True  # ✅ Only for one-way mode
```

### In `dispatcher.py` (for status checking):
```python
# OLD (BROKEN)
if result.get("status") in ["filled", "partially_filled", "pending"]:  # ❌

# NEW (WORKING)
if result.get("status") in ["filled", "partially_filled", "pending", "NEW"]:  # ✅
```

---

## Quick Verify

Run this in the K8s pod:
```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps deployment/petrosa-tradeengine -- python scripts/verify_sl_tp_fix.py
```

Expected output:
```
✅ SUCCESS: SL and TP orders were placed and verified on Binance!
```

---

## Files Changed

- `tradeengine/exchange/binance.py` - Lines 285-293, 323-331, 352-360, 393-401, 420-428, 465-473
- `tradeengine/dispatcher.py` - Lines 785-808, 866-890

---

## Key Points

1. ✅ **Hedge Mode**: Binance auto-sets `reduceOnly` based on side + positionSide
2. ✅ **Status**: SL/TP orders return `"NEW"` status when placed (not `"filled"`)
3. ✅ **Backward Compatible**: Works with both hedge mode and one-way mode
4. ✅ **Verified**: Tested and working on Binance TESTNET

---

## Deployment

1. Build image: `make build`
2. Push to registry: `docker push <registry>/petrosa-tradeengine:<version>`
3. Update deployment: `kubectl apply -f k8s/deployment.yaml`
4. Verify: Check logs for "✅ STOP LOSS PLACED" and "✅ TAKE PROFIT PLACED"

---

## More Info

- **Full Documentation**: `SL_TP_FIX_SUMMARY.md`
- **Implementation Details**: `IMPLEMENTATION_COMPLETE.md`
- **Test Script**: `scripts/test_sl_tp_hedge_mode.py`
- **Verify Script**: `scripts/verify_sl_tp_fix.py`
