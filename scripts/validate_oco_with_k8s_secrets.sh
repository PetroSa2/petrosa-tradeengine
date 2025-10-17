#!/bin/bash
#
# OCO Testnet Validation Using Kubernetes Secrets
#
# This script extracts API credentials from Kubernetes secrets and runs the OCO validation
#
# Prerequisites:
# - Access to the Kubernetes cluster (kubeconfig configured)
# - petrosa-sensitive-credentials secret exists
# - kubectl command available
#
# Usage:
#   ./scripts/validate_oco_with_k8s_secrets.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo "=============================================="
echo "🔐 OCO TESTNET VALIDATION (Using K8s Secrets)"
echo "=============================================="
echo ""

# Change to project root
cd "$(dirname "$0")/.."

# Set kubeconfig
export KUBECONFIG="k8s/kubeconfig.yaml"

echo "📍 Project: petrosa-tradeengine"
echo "📁 Location: $(pwd)"
echo "🔧 Kubeconfig: k8s/kubeconfig.yaml"
echo ""

# Check kubectl access
echo "=============================================="
echo "🔍 Checking Kubernetes Access"
echo "=============================================="
echo ""

if ! kubectl cluster-info &>/dev/null; then
    echo -e "${RED}❌ ERROR: Cannot connect to Kubernetes cluster${NC}"
    echo ""
    echo "Please check:"
    echo "  1. Your kubeconfig is correct: k8s/kubeconfig.yaml"
    echo "  2. The cluster is accessible"
    echo "  3. VPN is connected (if required)"
    echo ""
    exit 1
fi

echo -e "${GREEN}✅ Connected to Kubernetes cluster${NC}"
echo ""

# Check if secret exists
echo "=============================================="
echo "🔍 Checking for API Credentials Secret"
echo "=============================================="
echo ""

if ! kubectl get secret petrosa-sensitive-credentials -n petrosa-apps &>/dev/null; then
    echo -e "${RED}❌ ERROR: Secret 'petrosa-sensitive-credentials' not found${NC}"
    echo ""
    echo "The secret should exist in namespace: petrosa-apps"
    echo "Please verify the secret exists with:"
    echo "  kubectl get secret petrosa-sensitive-credentials -n petrosa-apps"
    echo ""
    exit 1
fi

echo -e "${GREEN}✅ Found secret: petrosa-sensitive-credentials${NC}"
echo ""

# Extract API credentials from Kubernetes secret
echo "=============================================="
echo "🔐 Extracting API Credentials from K8s"
echo "=============================================="
echo ""

# Extract API key
BINANCE_API_KEY=$(kubectl get secret petrosa-sensitive-credentials -n petrosa-apps \
    -o jsonpath='{.data.BINANCE_API_KEY}' | base64 -d)

# Extract API secret
BINANCE_API_SECRET=$(kubectl get secret petrosa-sensitive-credentials -n petrosa-apps \
    -o jsonpath='{.data.BINANCE_API_SECRET}' | base64 -d)

# Check if testnet flag is set
BINANCE_TESTNET=$(kubectl get secret petrosa-sensitive-credentials -n petrosa-apps \
    -o jsonpath='{.data.BINANCE_TESTNET}' | base64 -d 2>/dev/null || echo "false")

# Validate credentials were extracted
if [ -z "$BINANCE_API_KEY" ] || [ -z "$BINANCE_API_SECRET" ]; then
    echo -e "${RED}❌ ERROR: Failed to extract API credentials${NC}"
    echo ""
    echo "The secret might be missing the following keys:"
    echo "  - BINANCE_API_KEY"
    echo "  - BINANCE_API_SECRET"
    echo ""
    exit 1
fi

echo -e "${GREEN}✅ API credentials extracted successfully${NC}"
echo ""

# Show configuration
echo "=============================================="
echo "📋 Configuration"
echo "=============================================="
echo "API Key: ${BINANCE_API_KEY:0:10}...${BINANCE_API_KEY: -10}"
echo "API Secret: ${BINANCE_API_SECRET:0:10}...${BINANCE_API_SECRET: -10}"
echo "Testnet Mode: $BINANCE_TESTNET"
echo ""

# Check if testnet mode
if [ "$BINANCE_TESTNET" != "true" ]; then
    echo -e "${YELLOW}⚠️  WARNING: Testnet mode is NOT enabled in K8s secrets${NC}"
    echo -e "${YELLOW}⚠️  The secret has BINANCE_TESTNET=$BINANCE_TESTNET${NC}"
    echo ""
    echo "This script will force testnet mode for safety."
    echo "To test with production keys, use a different script."
    echo ""
fi

# Force testnet mode for safety
export BINANCE_TESTNET="true"

# Export credentials for the test scripts
export BINANCE_API_KEY
export BINANCE_API_SECRET

echo -e "${GREEN}✅ Credentials loaded from Kubernetes${NC}"
echo -e "${GREEN}✅ Forced testnet mode for safety${NC}"
echo ""

# Ask for confirmation
echo -e "${YELLOW}⚠️  This will place REAL orders on Binance TESTNET${NC}"
echo -e "${YELLOW}⚠️  Using credentials from K8s secret: petrosa-sensitive-credentials${NC}"
echo ""
read -p "Continue with testnet validation? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled by user"
    exit 0
fi

echo ""
echo "=============================================="
echo "🔍 Step 1: Testing Binance Connection"
echo "=============================================="
echo ""

python scripts/test-binance-futures-testnet.py

if [ $? -ne 0 ]; then
    echo ""
    echo -e "${RED}❌ Binance connection test failed${NC}"
    echo ""
    echo "This could mean:"
    echo "  1. API keys in K8s are for production, not testnet"
    echo "  2. API keys don't have Futures trading enabled"
    echo "  3. API keys have IP restrictions"
    echo ""
    echo "To check the keys in K8s:"
    echo "  kubectl get secret petrosa-sensitive-credentials -n petrosa-apps -o yaml"
    echo ""
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Binance connection successful${NC}"
echo ""

echo "=============================================="
echo "🚀 Step 2: Running OCO Live Test"
echo "=============================================="
echo ""

python scripts/live_oco_test.py

if [ $? -eq 0 ]; then
    echo ""
    echo "=============================================="
    echo -e "${GREEN}✅ OCO TESTNET VALIDATION SUCCESSFUL${NC}"
    echo "=============================================="
    echo ""
    echo "📊 Summary:"
    echo "   ✅ Credentials loaded from Kubernetes"
    echo "   ✅ OCO orders placed successfully"
    echo "   ✅ Orders verified on Binance"
    echo "   ✅ Monitoring system working"
    echo "   ✅ Cancellation working"
    echo "   ✅ Cleanup completed"
    echo ""
    echo -e "${GREEN}🎯 Your OCO implementation is READY!${NC}"
    echo ""
    echo "📝 Next Steps:"
    echo "   1. Review the test output above"
    echo "   2. Check your positions on testnet.binancefuture.com"
    echo "   3. Try triggering one order to see OCO behavior"
    echo "   4. When ready, deploy to production"
    echo ""
    echo "📁 Documentation:"
    echo "   - TESTNET_OCO_VALIDATION_GUIDE.md"
    echo "   - OCO_VALIDATION_COMPLETE_SUMMARY.md"
    echo ""
else
    echo ""
    echo "=============================================="
    echo -e "${RED}❌ OCO TESTNET VALIDATION FAILED${NC}"
    echo "=============================================="
    echo ""
    echo "📊 Troubleshooting:"
    echo "   1. Check the error messages above"
    echo "   2. Verify K8s secrets are testnet keys"
    echo "   3. Ensure testnet account has balance"
    echo "   4. Check API key permissions"
    echo ""
    echo "To verify K8s secrets:"
    echo "   kubectl get secret petrosa-sensitive-credentials -n petrosa-apps -o yaml"
    echo ""
    echo "📁 See: TESTNET_OCO_VALIDATION_GUIDE.md for detailed troubleshooting"
    echo ""
    exit 1
fi

echo "=============================================="
