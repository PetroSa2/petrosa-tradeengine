# Testnet OCO Validation - Quick Start

## 🚀 **Quick Start (5 Minutes)**

### **Step 1: Get Testnet API Keys** (2 minutes)

1. Go to https://testnet.binancefuture.com/
2. Login or register
3. Click on API Management
4. Create new API key
5. Save your API Key and Secret
6. Get free testnet USDT from the faucet

### **Step 2: Set Environment Variables** (1 minute)

```bash
export BINANCE_API_KEY="your-testnet-api-key-here"
export BINANCE_API_SECRET="your-testnet-api-secret-here"
```

### **Step 3: Run Validation** (2 minutes)

```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine
./scripts/validate_oco_testnet.sh
```

That's it! The script will:
- ✅ Test Binance connection
- ✅ Place OCO orders on testnet
- ✅ Verify orders on Binance
- ✅ Test monitoring system
- ✅ Test cancellation
- ✅ Clean up

---

## 📊 **What You'll See**

### **Expected Output:**

```
==============================================
🧪 OCO TESTNET VALIDATION
==============================================

✅ API credentials found
✅ Testnet mode enabled

==============================================
🔍 Step 1: Testing Binance Connection
==============================================

✅ Environment variables configured correctly
✅ Futures client initialized successfully
✅ API connection successful
✅ Exchange info retrieved successfully
✅ Account info retrieved successfully
✅ Market data retrieved for BTCUSDT
Current price: $50,234.50

==============================================
🚀 Step 2: Running OCO Live Test
==============================================

🚀 LIVE OCO IMPLEMENTATION TEST
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
✅ OCO PAIR TRACKED CORRECTLY

📊 TEST 4: VERIFYING ORDER MONITORING
✅ ORDER MONITORING IS ACTIVE

📊 TEST 5: VERIFYING ORDERS ON BINANCE
✅ SL ORDER FOUND ON BINANCE
✅ TP ORDER FOUND ON BINANCE
✅ BOTH ORDERS VERIFIED ON BINANCE

📊 TEST 6: TESTING ORDER MONITORING
✅ ORDER MONITORING TEST COMPLETED

📊 TEST 7: TESTING MANUAL OCO CANCELLATION
✅ OCO PAIR CANCELLED SUCCESSFULLY
✅ BOTH ORDERS CANCELLED ON BINANCE

📊 TEST 8: VERIFYING CLEANUP
✅ OCO PAIR REMOVED FROM TRACKING

==============================================
✅ OCO TESTNET VALIDATION SUCCESSFUL
==============================================

🎯 Your OCO implementation is READY!
```

---

## 🔍 **Verify on Binance UI**

### **During the test, check Binance:**

1. Go to https://testnet.binancefuture.com/
2. Navigate to **Futures** → **Open Orders**
3. You should see **2 orders**:
   - STOP_MARKET (your SL)
   - TAKE_PROFIT_MARKET (your TP)

### **After the test:**

- Both orders should be **cancelled**
- No orphaned orders remaining
- Position closed

---

## ✅ **Success Criteria**

Your OCO implementation is working if you see:

- ✅ Both SL and TP orders placed
- ✅ Orders visible on Binance
- ✅ Monitoring system active
- ✅ Orders cancelled successfully
- ✅ Cleanup completed

---

## 🚨 **Troubleshooting**

### **Problem: API Connection Failed**

```bash
❌ API connection failed: Invalid API key
```

**Solution:**
- Verify keys are from **testnet.binancefuture.com** (not mainnet)
- Check API permissions include Futures trading
- Ensure you copied the full key and secret

### **Problem: Insufficient Balance**

```bash
❌ Error: Insufficient balance
```

**Solution:**
- Go to testnet.binancefuture.com
- Use the faucet to get free testnet USDT
- Wait a few minutes for balance to update

### **Problem: Position Mode Error**

```bash
❌ Error: Invalid position side
```

**Solution:**
Enable hedge mode on your testnet account:
- Go to Account Settings
- Enable "Hedge Mode"
- Or run: `python scripts/verify_hedge_mode.py`

---

## 📝 **Alternative: Manual Step-by-Step**

If the automated script doesn't work, run manually:

```bash
# Step 1: Test connection
python scripts/test-binance-futures-testnet.py

# Step 2: Run OCO test
python scripts/live_oco_test.py

# Step 3: Check positions
python scripts/query_binance_positions.py
```

---

## 📁 **More Information**

For detailed guide, see:
- `/TESTNET_OCO_VALIDATION_GUIDE.md` - Complete validation guide
- `/OCO_QUICK_TEST_GUIDE.md` - Quick reference
- `/OCO_TEST_RESULTS_SUMMARY.md` - Unit test results

---

## 🎯 **What This Validates**

| Test | What It Checks | Expected Result |
|------|----------------|-----------------|
| Connection | Binance API access | ✅ Connected |
| Order Placement | Create SL + TP orders | ✅ Both created |
| Order Verification | Orders exist on Binance | ✅ Both visible |
| OCO Tracking | System tracks OCO pair | ✅ Tracked |
| Monitoring | Background task running | ✅ Active |
| Cancellation | Can cancel both orders | ✅ Both cancelled |
| Cleanup | OCO pair removed | ✅ Cleaned up |

---

## 🚀 **After Successful Validation**

Once testnet validation passes:

1. ✅ **Save the logs** - Keep evidence of successful test
2. ✅ **Test triggering** - Let one order fill naturally
3. ✅ **Verify OCO behavior** - Check other order cancels
4. 🔜 **Small live test** - Very small position on mainnet
5. 🔜 **Full deployment** - Deploy to production

---

**Estimated Time**: 5-10 minutes
**Difficulty**: Easy
**Cost**: Free (testnet only)

**Ready? Let's validate! 🚀**

```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine
./scripts/validate_oco_testnet.sh
```
