# 🔐 OCO Testnet Validation Using Kubernetes Secrets

## 🎯 **Quick Start (Using K8s Secrets)**

Since your Binance API keys are stored in Kubernetes, use this simplified approach:

---

## 🚀 **One-Command Validation**

```bash
./scripts/validate_oco_with_k8s_secrets.sh
```

That's it! The script will:
1. ✅ Connect to your Kubernetes cluster
2. ✅ Extract API credentials from the `petrosa-sensitive-credentials` secret
3. ✅ Force testnet mode for safety
4. ✅ Run the complete OCO validation
5. ✅ Report results

**Time**: 5-10 minutes
**Prerequisites**: Kubernetes cluster access

---

## 📋 **What the Script Does**

### **Step 1: Extract Credentials from K8s**

The script automatically extracts:
- `BINANCE_API_KEY` from K8s secret
- `BINANCE_API_SECRET` from K8s secret
- Sets `BINANCE_TESTNET=true` (forced for safety)

**K8s Secret Used:**
- Namespace: `petrosa-apps`
- Secret: `petrosa-sensitive-credentials`

### **Step 2: Run Validation**

Same validation as the manual method:
1. Test Binance connection
2. Place OCO orders (SL + TP)
3. Verify orders on Binance
4. Test monitoring system
5. Test cancellation
6. Clean up

---

## ✅ **Expected Output**

```bash
==============================================
🔐 OCO TESTNET VALIDATION (Using K8s Secrets)
==============================================

📍 Project: petrosa-tradeengine
🔧 Kubeconfig: k8s/kubeconfig.yaml

==============================================
🔍 Checking Kubernetes Access
==============================================

✅ Connected to Kubernetes cluster

==============================================
🔍 Checking for API Credentials Secret
==============================================

✅ Found secret: petrosa-sensitive-credentials

==============================================
🔐 Extracting API Credentials from K8s
==============================================

✅ API credentials extracted successfully

==============================================
📋 Configuration
==============================================
API Key: <masked>
API Secret: xxxxxxxxxx...
Testnet Mode: true

✅ Credentials loaded from Kubernetes
✅ Forced testnet mode for safety

⚠️  This will place REAL orders on Binance TESTNET
⚠️  Using credentials from K8s secret: petrosa-sensitive-credentials

Continue with testnet validation? (y/n) y

==============================================
🔍 Step 1: Testing Binance Connection
==============================================

✅ API connection successful
✅ Account info retrieved successfully

==============================================
🚀 Step 2: Running OCO Live Test
==============================================

🚀 LIVE OCO IMPLEMENTATION TEST
📊 GETTING CURRENT MARKET PRICES
BTCUSDT Current Price: $50,234.50

📊 TEST 2: PLACING OCO ORDERS DIRECTLY
✅ OCO ORDERS PLACED SUCCESSFULLY
  SL Order ID: 123456789
  TP Order ID: 123456790

📊 TEST 5: VERIFYING ORDERS ON BINANCE
✅ SL ORDER FOUND ON BINANCE
✅ TP ORDER FOUND ON BINANCE
✅ BOTH ORDERS VERIFIED ON BINANCE

📊 TEST 7: TESTING MANUAL OCO CANCELLATION
✅ OCO PAIR CANCELLED SUCCESSFULLY
✅ BOTH ORDERS CANCELLED ON BINANCE

==============================================
✅ OCO TESTNET VALIDATION SUCCESSFUL
==============================================

🎯 Your OCO implementation is READY!
```

---

## 🔍 **Verifying Your K8s Secrets**

### **Check if secrets exist:**

```bash
# Check the secret
kubectl --kubeconfig=k8s/kubeconfig.yaml get secret petrosa-sensitive-credentials -n petrosa-apps

# View secret contents (base64 encoded)
kubectl --kubeconfig=k8s/kubeconfig.yaml get secret petrosa-sensitive-credentials -n petrosa-apps -o yaml

# Decode API key (first 20 chars)
kubectl --kubeconfig=k8s/kubeconfig.yaml get secret petrosa-sensitive-credentials -n petrosa-apps \
  -o jsonpath='{.data.BINANCE_API_KEY}' | base64 -d | head -c 20
```

### **What should be in the secret:**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: petrosa-sensitive-credentials
  namespace: petrosa-apps
type: Opaque
data:
  BINANCE_API_KEY: <base64-encoded-key>
  BINANCE_API_SECRET: <base64-encoded-secret>
  BINANCE_TESTNET: dHJ1ZQ==  # "true" in base64
```

---

## ⚠️ **Important Notes**

### **Testnet vs Production Keys**

The script checks if `BINANCE_TESTNET` is set in your K8s secret:

**If BINANCE_TESTNET=true:**
- ✅ Script uses testnet mode
- ✅ Safe to test

**If BINANCE_TESTNET=false or not set:**
- ⚠️ Script **FORCES testnet mode** for safety
- ⚠️ Will show warning
- ✅ Won't use production keys

### **Production Keys Warning**

If your K8s secrets contain **production API keys**:
- The script will still force testnet mode
- The connection test will **fail** (production keys won't work on testnet)
- You'll need separate testnet keys

**Solution:** Create a separate testnet secret or use manual validation with testnet keys.

---

## 🚨 **Troubleshooting**

### **Issue 1: Cannot Connect to K8s**

```bash
❌ ERROR: Cannot connect to Kubernetes cluster
```

**Solution:**
```bash
# Check cluster connection
kubectl --kubeconfig=k8s/kubeconfig.yaml cluster-info

# Check if VPN is needed
# Connect to VPN if required

# Verify kubeconfig
cat k8s/kubeconfig.yaml
```

### **Issue 2: Secret Not Found**

```bash
❌ ERROR: Secret 'petrosa-sensitive-credentials' not found
```

**Solution:**
```bash
# List all secrets
kubectl --kubeconfig=k8s/kubeconfig.yaml get secrets -n petrosa-apps

# Check if namespace exists
kubectl --kubeconfig=k8s/kubeconfig.yaml get namespace petrosa-apps

# Create secret if missing (with testnet keys)
kubectl --kubeconfig=k8s/kubeconfig.yaml create secret generic petrosa-sensitive-credentials \
  --from-literal=BINANCE_API_KEY="your-testnet-key" \
  --from-literal=BINANCE_API_SECRET="your-testnet-secret" \
  --from-literal=BINANCE_TESTNET="true" \
  -n petrosa-apps
```

### **Issue 3: API Connection Failed (Production Keys)**

```bash
❌ Binance connection test failed
```

**This means:** Your K8s secrets contain production keys, not testnet keys.

**Solutions:**

**Option A: Create Testnet Secret (Recommended)**
```bash
# Create a separate testnet secret
kubectl --kubeconfig=k8s/kubeconfig.yaml create secret generic petrosa-testnet-credentials \
  --from-literal=BINANCE_API_KEY="testnet-key" \
  --from-literal=BINANCE_API_SECRET="testnet-secret" \
  --from-literal=BINANCE_TESTNET="true" \
  -n petrosa-apps

# Modify script to use this secret
```

**Option B: Use Manual Method**
```bash
# Export testnet keys manually
export BINANCE_API_KEY="your-testnet-key"
export BINANCE_API_SECRET="your-testnet-secret"
export BINANCE_TESTNET="true"

# Run standard validation
./scripts/validate_oco_testnet.sh
```

---

## 📊 **Comparison: K8s vs Manual**

| Method | When to Use | Pros | Cons |
|--------|-------------|------|------|
| **K8s Secrets** | You have K8s access | ✅ No manual key entry<br>✅ Uses production setup<br>✅ One command | ⚠️ Requires K8s access<br>⚠️ May have production keys |
| **Manual Export** | Quick testing | ✅ Simple<br>✅ Works anywhere<br>✅ Use any keys | ⚠️ Manual key entry<br>⚠️ Keys in shell history |

---

## 🎯 **Which Method Should You Use?**

### **Use K8s Secrets Method If:**
- ✅ You have Kubernetes cluster access
- ✅ Your K8s secrets contain **testnet** API keys
- ✅ You want to test with production infrastructure setup

```bash
./scripts/validate_oco_with_k8s_secrets.sh
```

### **Use Manual Method If:**
- ✅ K8s secrets contain **production** keys only
- ✅ You want to use separate testnet keys
- ✅ You don't have K8s access currently

```bash
export BINANCE_API_KEY="testnet-key"
export BINANCE_API_SECRET="testnet-secret"
./scripts/validate_oco_testnet.sh
```

---

## 📝 **Recommended Approach**

Since you have K8s secrets, here's the recommended flow:

### **Step 1: Check What Keys You Have**

```bash
# Check if testnet flag is set
kubectl --kubeconfig=k8s/kubeconfig.yaml get secret petrosa-sensitive-credentials -n petrosa-apps \
  -o jsonpath='{.data.BINANCE_TESTNET}' | base64 -d
```

### **Step 2A: If You Have Testnet Keys**

```bash
# Just run the K8s validation
./scripts/validate_oco_with_k8s_secrets.sh
```

### **Step 2B: If You Have Production Keys**

```bash
# Get testnet keys from testnet.binancefuture.com
# Then use manual method:
export BINANCE_API_KEY="your-testnet-key"
export BINANCE_API_SECRET="your-testnet-secret"
export BINANCE_TESTNET="true"
./scripts/validate_oco_testnet.sh
```

---

## ✅ **Success Criteria**

Validation is successful when you see:

- ✅ Credentials extracted from K8s
- ✅ Connection to Binance successful
- ✅ OCO orders placed
- ✅ Both orders verified on Binance
- ✅ Monitoring system active
- ✅ Cancellation successful
- ✅ Cleanup complete

---

## 🚀 **Quick Commands**

```bash
# Check K8s connection
kubectl cluster-info

# Check if secret exists
kubectl get secret petrosa-sensitive-credentials -n petrosa-apps

# Run validation with K8s secrets
./scripts/validate_oco_with_k8s_secrets.sh

# OR use manual method with your own testnet keys
export BINANCE_API_KEY="testnet-key"
export BINANCE_API_SECRET="testnet-secret"
./scripts/validate_oco_testnet.sh
```

---

## 📁 **Related Files**

- **This guide**: `TESTNET_VALIDATION_K8S.md` (for K8s users)
- **Standard guide**: `TESTNET_OCO_VALIDATION_GUIDE.md` (for manual keys)
- **K8s script**: `scripts/validate_oco_with_k8s_secrets.sh`
- **Manual script**: `scripts/validate_oco_testnet.sh`

---

**Ready to validate with your K8s secrets? 🚀**

```bash
./scripts/validate_oco_with_k8s_secrets.sh
```
