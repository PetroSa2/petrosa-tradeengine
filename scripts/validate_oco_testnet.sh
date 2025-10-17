#!/bin/bash
#
# OCO Testnet Validation Script
#
# This script validates the OCO (One-Cancels-the-Other) functionality on Binance Testnet
#
# Prerequisites:
# - Binance Testnet API keys (from testnet.binancefuture.com)
# - Testnet balance (get free USDT from faucet)
# - Environment variables set (BINANCE_API_KEY, BINANCE_API_SECRET)
#
# Usage:
#   ./scripts/validate_oco_testnet.sh
#
# Or with inline credentials:
#   BINANCE_API_KEY="..." BINANCE_API_SECRET="..." ./scripts/validate_oco_testnet.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo "=============================================="
echo "🧪 OCO TESTNET VALIDATION"
echo "=============================================="
echo ""

# Change to project root
cd "$(dirname "$0")/.."

# Check for API credentials
if [ -z "$BINANCE_API_KEY" ] || [ -z "$BINANCE_API_SECRET" ]; then
    echo -e "${RED}❌ ERROR: Missing API credentials${NC}"
    echo ""
    echo "Please set your Binance Testnet API credentials:"
    echo ""
    echo "  export BINANCE_API_KEY=\"your-testnet-api-key\""
    echo "  export BINANCE_API_SECRET=\"your-testnet-api-secret\""
    echo ""
    echo "Get testnet API keys from: https://testnet.binancefuture.com/"
    echo ""
    exit 1
fi

# Set testnet mode
export BINANCE_TESTNET="true"

echo -e "${GREEN}✅ API credentials found${NC}"
echo -e "${GREEN}✅ Testnet mode enabled${NC}"
echo ""

# Show configuration
echo "=============================================="
echo "📋 Configuration"
echo "=============================================="
echo "API Key: ${BINANCE_API_KEY:0:8}...${BINANCE_API_KEY: -8}"
echo "Testnet: true"
echo "Environment: testnet"
echo ""

# Ask for confirmation
echo -e "${YELLOW}⚠️  This will place REAL orders on Binance TESTNET${NC}"
echo -e "${YELLOW}⚠️  Small test positions will be opened and closed${NC}"
echo ""
read -p "Continue? (y/n) " -n 1 -r
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
    echo "Please check your API credentials and try again"
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
    echo "   4. When ready, test on mainnet with small positions"
    echo ""
    echo "📁 Documentation:"
    echo "   - TESTNET_OCO_VALIDATION_GUIDE.md"
    echo "   - OCO_QUICK_TEST_GUIDE.md"
    echo ""
else
    echo ""
    echo "=============================================="
    echo -e "${RED}❌ OCO TESTNET VALIDATION FAILED${NC}"
    echo "=============================================="
    echo ""
    echo "📊 Troubleshooting:"
    echo "   1. Check the error messages above"
    echo "   2. Verify you have testnet balance (get from faucet)"
    echo "   3. Ensure hedge mode is enabled"
    echo "   4. Check API key permissions"
    echo ""
    echo "📁 See: TESTNET_OCO_VALIDATION_GUIDE.md for detailed troubleshooting"
    echo ""
    exit 1
fi

echo "=============================================="
