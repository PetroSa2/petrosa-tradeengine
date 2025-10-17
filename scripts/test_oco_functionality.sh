#!/bin/bash
#
# Quick OCO Functionality Test Script
# Tests the core OCO (One-Cancels-the-Other) functionality
#
# Usage: ./scripts/test_oco_functionality.sh

set -e

echo "=============================================="
echo "🧪 OCO FUNCTIONALITY TEST"
echo "=============================================="
echo ""

# Change to project root
cd "$(dirname "$0")/.."

echo "📍 Project: petrosa-tradeengine"
echo "📁 Location: $(pwd)"
echo ""

echo "=============================================="
echo "🔍 Running Core OCO Tests"
echo "=============================================="
echo ""

# Run core OCO tests
echo "✅ Test 1: Place OCO orders for LONG position"
python -m pytest tests/test_oco_orders.py::test_place_oco_orders_long_position -v --tb=short

echo ""
echo "✅ Test 2: Place OCO orders for SHORT position"
python -m pytest tests/test_oco_orders.py::test_place_oco_orders_short_position -v --tb=short

echo ""
echo "✅ Test 3: Cancel TP when SL fills (OCO behavior)"
python -m pytest tests/test_oco_orders.py::test_cancel_other_order_when_sl_fills -v --tb=short

echo ""
echo "✅ Test 4: Cancel SL when TP fills (OCO behavior)"
python -m pytest tests/test_oco_orders.py::test_cancel_other_order_when_tp_fills -v --tb=short

echo ""
echo "✅ Test 5: Monitor and detect filled orders"
python -m pytest tests/test_oco_orders.py::test_oco_monitoring_detects_filled_order -v --tb=short

echo ""
echo "=============================================="
echo "✅ CORE OCO TESTS COMPLETE"
echo "=============================================="
echo ""
echo "📊 Summary:"
echo "   - OCO orders can be placed ✅"
echo "   - SL fills → TP cancelled ✅"
echo "   - TP fills → SL cancelled ✅"
echo "   - Monitoring system works ✅"
echo ""
echo "🎯 Result: OCO FUNCTIONALITY VERIFIED"
echo ""
echo "📝 Next Steps:"
echo "   1. Review test results above"
echo "   2. Test on Binance Testnet"
echo "   3. Validate with real orders"
echo ""
echo "📁 Documentation:"
echo "   - /OCO_QUICK_TEST_GUIDE.md"
echo "   - /OCO_TEST_RESULTS_SUMMARY.md"
echo ""
echo "=============================================="
