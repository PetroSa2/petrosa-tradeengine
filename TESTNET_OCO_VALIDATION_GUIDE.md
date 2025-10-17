# Testnet OCO Validation Guide

## 🎯 **Objective**

Validate the OCO (One-Cancels-the-Other) functionality on Binance Testnet to ensure:
1. Positions are created successfully
2. Both SL and TP orders are placed
3. When one order fills, the other is automatically cancelled
4. System behaves correctly in real-world conditions

---

## 📋 **Prerequisites**

### **1. Binance Testnet API Keys**

You need testnet API keys from Binance:
- Go to: https://testnet.binancefuture.com/
- Login/Register
- Get API Key and Secret
- Enable Futures Trading

### **2. Environment Setup**

Set your testnet credentials:

```bash
export BINANCE_API_KEY="your-testnet-api-key"
export BINANCE_API_SECRET="your-testnet-api-secret"
export BINANCE_TESTNET="true"
```

Or create a `.env` file:

```bash
# .env.testnet
BINANCE_API_KEY=your-testnet-api-key
BINANCE_API_SECRET=your-testnet-api-secret
BINANCE_TESTNET=true
ENVIRONMENT=testnet
```

### **3. Testnet Balance**

- Login to https://testnet.binancefuture.com/
- Get free testnet USDT from the faucet
- Recommended: At least 1000 USDT for testing

---

## 🚀 **Quick Testnet Validation**

### **Option 1: Automated Test (Recommended)**

Run the comprehensive OCO test script:

```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine

# Set testnet credentials
export BINANCE_API_KEY="your-testnet-api-key"
export BINANCE_API_SECRET="your-testnet-api-secret"
export BINANCE_TESTNET="true"

# Run the OCO testnet validation
python scripts/live_oco_test.py
```

This script will:
1. ✅ Get current market prices
2. ✅ Place OCO orders (SL + TP)
3. ✅ Verify orders on Binance
4. ✅ Test monitoring system
5. ✅ Test manual cancellation
6. ✅ Verify cleanup

### **Option 2: Step-by-Step Manual Test**

Run the validation step by step:

```bash
# Step 1: Test Binance connection
python scripts/test-binance-futures-testnet.py

# Step 2: Run OCO live test
python scripts/live_oco_test.py

# Step 3: Check positions
python scripts/query_binance_positions.py
```

---

## 📊 **What the Test Does**

### **Test 1: OCO Order Placement**

```
1. Get current BTC price (e.g., $50,000)
2. Calculate SL price ($49,500 - 1% below)
3. Calculate TP price ($50,500 - 1% above)
4. Open very small position (0.001 BTC)
5. Place SL order at $49,500
6. Place TP order at $50,500
7. Verify both orders exist on Binance
```

**Expected Result**: ✅ Both orders visible in Binance "Open Orders"

### **Test 2: Order Monitoring**

```
1. Background task monitors orders every 2 seconds
2. Checks if orders still exist
3. Detects when one order fills
4. Triggers cancellation of other order
```

**Expected Result**: ✅ Monitoring system running

### **Test 3: Manual Cancellation**

```
1. Call cancel_oco_pair()
2. Both orders cancelled on Binance
3. OCO pair removed from tracking
```

**Expected Result**: ✅ Both orders cancelled, pair cleaned up

### **Test 4: Signal Integration**

```
1. Create trading signal with SL/TP
2. Process signal through dispatcher
3. Verify OCO orders placed automatically
```

**Expected Result**: ✅ OCO orders created from signal

---

## 🔍 **Manual Verification on Binance**

### **Step 1: Check Open Orders**

1. Go to https://testnet.binancefuture.com/
2. Navigate to "Futures" → "Open Orders"
3. You should see **2 orders**:
   - 1 STOP_MARKET order (your SL)
   - 1 TAKE_PROFIT_MARKET order (your TP)

### **Step 2: Verify Order Details**

Click on each order and verify:

**Stop Loss Order:**
- Type: STOP_MARKET
- Side: SELL (for LONG) or BUY (for SHORT)
- Trigger Price: Your calculated SL price
- Reduce Only: true
- Position Side: LONG or SHORT

**Take Profit Order:**
- Type: TAKE_PROFIT_MARKET
- Side: SELL (for LONG) or BUY (for SHORT)
- Trigger Price: Your calculated TP price
- Reduce Only: true
- Position Side: LONG or SHORT

### **Step 3: Wait for One to Fill**

**Option A - Natural Market Movement:**
- Wait for price to move and hit either SL or TP
- When one fills, check if the other is automatically cancelled

**Option B - Manual Trigger (Advanced):**
- Manually move the price by placing large market orders
- This is advanced and not recommended for beginners

---

## ✅ **Success Criteria**

Your OCO implementation is working correctly if:

| Criteria | Expected | Verification |
|----------|----------|--------------|
| Position Created | ✅ | Check "Positions" tab on Binance |
| SL Order Placed | ✅ | Check "Open Orders" - should see STOP_MARKET |
| TP Order Placed | ✅ | Check "Open Orders" - should see TAKE_PROFIT_MARKET |
| Both Orders Active | ✅ | Both visible simultaneously |
| Monitoring Running | ✅ | Script shows "Monitoring active: True" |
| **SL Fills → TP Cancelled** | ✅ | **When SL fills, TP disappears from Open Orders** |
| **TP Fills → SL Cancelled** | ✅ | **When TP fills, SL disappears from Open Orders** |
| Cleanup Complete | ✅ | OCO pair removed from tracking |

---

## 📝 **Expected Script Output**

When running `python scripts/live_oco_test.py`, you should see:

```
================================================================================
🚀 LIVE OCO IMPLEMENTATION TEST
================================================================================

📊 GETTING CURRENT MARKET PRICES
BTCUSDT Current Price: $50,234.50

📊 TEST 1: CREATING REALISTIC SIGNAL WITH CURRENT PRICES
Position ID: live_test_1729180800
Stop Loss: $49,983.33 (-0.50%)
Take Profit: $50,736.85 (+1.00%)

📊 TEST 2: PLACING OCO ORDERS DIRECTLY
✅ OCO ORDERS PLACED SUCCESSFULLY
  SL Order ID: 123456789
  TP Order ID: 123456790

📊 TEST 3: VERIFYING OCO PAIR TRACKING
Active OCO pairs: 1
✅ OCO PAIR TRACKED CORRECTLY
  Position ID: live_test_1729180800
  Symbol: BTCUSDT
  SL Order ID: 123456789
  TP Order ID: 123456790
  Status: active

📊 TEST 4: VERIFYING ORDER MONITORING
OCO Monitoring Active: True
✅ ORDER MONITORING IS ACTIVE

📊 TEST 5: VERIFYING ORDERS ON BINANCE
Total open orders for BTCUSDT: 2
✅ SL ORDER FOUND ON BINANCE
  Order ID: 123456789
  Type: STOP_MARKET
  Side: SELL
  Status: NEW
  Stop Price: 49983.33
  Quantity: 0.001
  Reduce Only: True
✅ TP ORDER FOUND ON BINANCE
  Order ID: 123456790
  Type: TAKE_PROFIT_MARKET
  Side: SELL
  Status: NEW
  Stop Price: 50736.85
  Quantity: 0.001
  Reduce Only: True
✅ BOTH ORDERS VERIFIED ON BINANCE

📊 TEST 6: TESTING ORDER MONITORING
⏳ Monitoring orders for 15 seconds...
  Monitoring check 1/3...
    SL Order Exists: True
    TP Order Exists: True
  Monitoring check 2/3...
    SL Order Exists: True
    TP Order Exists: True
  Monitoring check 3/3...
    SL Order Exists: True
    TP Order Exists: True
✅ ORDER MONITORING TEST COMPLETED

📊 TEST 7: TESTING MANUAL OCO CANCELLATION
✅ OCO PAIR CANCELLED SUCCESSFULLY
✅ BOTH ORDERS CANCELLED ON BINANCE

📊 TEST 8: VERIFYING CLEANUP
✅ OCO PAIR REMOVED FROM TRACKING
Remaining OCO pairs: 0

================================================================================
🎉 LIVE OCO IMPLEMENTATION TEST COMPLETED
================================================================================

📊 FINAL STATUS CHECK
Active OCO pairs: 0
Monitoring active: False

✅ ALL TESTS COMPLETED SUCCESSFULLY
🚀 OCO IMPLEMENTATION IS READY FOR DEPLOYMENT!
```

---

## 🚨 **Troubleshooting**

### **Issue 1: API Connection Failed**

```
❌ API connection failed: Invalid API key
```

**Solution:**
- Verify your API keys are from testnet.binancefuture.com
- Check API keys have Futures trading enabled
- Ensure `BINANCE_TESTNET=true` is set

### **Issue 2: Orders Not Appearing**

```
❌ SL ORDER NOT FOUND ON BINANCE
❌ TP ORDER NOT FOUND ON BINANCE
```

**Solution:**
- Check if orders were actually placed (look for errors in logs)
- Verify you have sufficient testnet balance
- Check position mode is set to HEDGE mode
- Ensure minimum notional requirements are met

### **Issue 3: Insufficient Balance**

```
❌ Error: Insufficient balance
```

**Solution:**
- Go to testnet.binancefuture.com
- Use the faucet to get free testnet USDT
- Wait a few minutes for balance to update

### **Issue 4: Position Side Error**

```
❌ Error: Invalid position side
```

**Solution:**
- Ensure hedge mode is enabled on your testnet account
- Set position mode to HEDGE:
  ```python
  client.futures_change_position_mode(dualSidePosition=True)
  ```

### **Issue 5: Monitoring Not Active**

```
⚠️ ORDER MONITORING NOT ACTIVE
```

**Solution:**
- This is usually okay if no OCO pairs exist
- Monitoring starts automatically when first OCO pair is created
- Check logs for any errors during monitoring initialization

---

## 📊 **Validation Checklist**

Use this checklist to validate your OCO implementation:

### **Before Running Tests:**
- [ ] Testnet API keys obtained
- [ ] Environment variables set
- [ ] Testnet balance available (>1000 USDT)
- [ ] Hedge mode enabled on testnet account

### **During Tests:**
- [ ] Script runs without errors
- [ ] Both SL and TP orders created
- [ ] Orders visible on Binance UI
- [ ] Order parameters correct (price, quantity, side)
- [ ] Monitoring system active

### **After Tests:**
- [ ] Verified one order fills → other cancelled
- [ ] Cleanup successful
- [ ] No orphaned orders remaining
- [ ] Logs show expected behavior

---

## 🎯 **Next Steps After Testnet Validation**

Once testnet validation is successful:

1. ✅ **Document results** - Save logs and screenshots
2. ✅ **Review any issues** - Fix any problems found
3. ✅ **Test edge cases** - Multiple positions, fast fills, etc.
4. 🔜 **Small live test** - Very small position on mainnet
5. 🔜 **Full deployment** - Deploy to production

---

## 📁 **Related Files**

- `/scripts/live_oco_test.py` - Main OCO testnet validation script
- `/scripts/test-binance-futures-testnet.py` - Testnet connection tester
- `/scripts/query_binance_positions.py` - Check open positions
- `/OCO_QUICK_TEST_GUIDE.md` - Quick reference guide
- `/OCO_TEST_RESULTS_SUMMARY.md` - Unit test results

---

## 🔗 **Useful Links**

- **Binance Futures Testnet**: https://testnet.binancefuture.com/
- **API Documentation**: https://binance-docs.github.io/apidocs/futures/en/
- **Get Testnet Funds**: https://testnet.binancefuture.com/ (login and use faucet)

---

**Last Updated**: October 17, 2025
**Status**: Ready for testnet validation
**Next**: Run `python scripts/live_oco_test.py` with testnet credentials
