#!/bin/bash
#
# Verify Hedge Mode Implementation
#
# This script verifies all changes for hedge mode position tracking before deployment
#

set -e

echo "============================================"
echo "Hedge Mode Implementation Verification"
echo "============================================"
echo ""

ERRORS=0
WARNINGS=0

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    ERRORS=$((ERRORS + 1))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

# Check 1: Verify all required files exist
echo "Checking required files..."

FILES=(
    "scripts/create_positions_table.sql"
    "shared/mysql_client.py"
    "scripts/verify_hedge_mode.py"
    "tradeengine/metrics.py"
    "tests/test_position_tracking.py"
    "docs/HEDGE_MODE_POSITION_TRACKING.md"
    "HEDGE_MODE_IMPLEMENTATION_SUMMARY.md"
    "k8s/mysql-schema-job.yaml"
    "scripts/deploy-with-mysql-init.sh"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        check_pass "File exists: $file"
    else
        check_fail "File missing: $file"
    fi
done
echo ""

# Check 2: Verify code modifications
echo "Checking code modifications..."

# Check TradeOrder has new fields
if grep -q "position_id.*str.*None" contracts/order.py && \
   grep -q "position_side.*str.*None" contracts/order.py && \
   grep -q "exchange.*str" contracts/order.py && \
   grep -q "strategy_metadata.*dict" contracts/order.py; then
    check_pass "TradeOrder contract updated with position tracking fields"
else
    check_fail "TradeOrder contract missing position tracking fields"
fi

# Check Binance client has positionSide support
if grep -q 'positionSide' tradeengine/exchange/binance.py; then
    check_pass "Binance client updated with positionSide parameter"
else
    check_fail "Binance client missing positionSide parameter"
fi

# Check dispatcher generates position IDs
if grep -q 'uuid.uuid4()' tradeengine/dispatcher.py && \
   grep -q 'position_id.*=.*str(uuid.uuid4())' tradeengine/dispatcher.py; then
    check_pass "Dispatcher generates position IDs"
else
    check_fail "Dispatcher missing position ID generation"
fi

# Check position manager has new methods
if grep -q 'create_position_record' tradeengine/position_manager.py && \
   grep -q 'close_position_record' tradeengine/position_manager.py && \
   grep -q '_export_position_opened_metrics' tradeengine/position_manager.py && \
   grep -q '_export_position_closed_metrics' tradeengine/position_manager.py; then
    check_pass "Position manager has position tracking methods"
else
    check_fail "Position manager missing position tracking methods"
fi

# Check MySQL client imported
if grep -q 'from shared.mysql_client import mysql_client' tradeengine/position_manager.py; then
    check_pass "MySQL client imported in position manager"
else
    check_warn "MySQL client import not found (may be handled differently)"
fi

# Check metrics module
if grep -q 'positions_opened_total' tradeengine/metrics.py && \
   grep -q 'positions_closed_total' tradeengine/metrics.py && \
   grep -q 'position_pnl_usd' tradeengine/metrics.py; then
    check_pass "Position metrics defined"
else
    check_fail "Position metrics not properly defined"
fi

# Check metrics follow the correct pattern
if grep -q 'from prometheus_client import' tradeengine/metrics.py; then
    check_pass "Metrics use prometheus_client (correct pattern)"
else
    check_fail "Metrics not using prometheus_client pattern"
fi

echo ""

# Check 3: Verify K8s configuration
echo "Checking Kubernetes configuration..."

if grep -q 'MYSQL_URI' k8s/deployment.yaml && \
   grep -q 'petrosa-sensitive-credentials' k8s/deployment.yaml; then
    check_pass "MYSQL_URI configured in deployment"
else
    check_fail "MYSQL_URI not configured in deployment"
fi

if grep -q 'HEDGE_MODE_ENABLED' k8s/deployment.yaml; then
    check_pass "HEDGE_MODE_ENABLED configured in deployment"
else
    check_fail "HEDGE_MODE_ENABLED not configured in deployment"
fi

if grep -q 'POSITION_MODE' k8s/deployment.yaml && grep -A 1 'POSITION_MODE' k8s/deployment.yaml | grep -q 'hedge'; then
    check_pass "POSITION_MODE set to hedge in deployment"
else
    check_fail "POSITION_MODE not set to hedge"
fi

echo ""

# Check 4: Verify constants
echo "Checking constants and environment..."

if grep -q 'HEDGE_MODE_ENABLED' shared/constants.py && \
   grep -q 'POSITION_MODE' shared/constants.py; then
    check_pass "Hedge mode constants defined"
else
    check_fail "Hedge mode constants not defined"
fi

if grep -q 'MYSQL_URI' env.example; then
    check_pass "MYSQL_URI in env.example"
else
    check_fail "MYSQL_URI not in env.example"
fi

echo ""

# Check 5: Run linting
echo "Running linters..."

if command -v ruff &> /dev/null; then
    if ruff check shared/mysql_client.py tradeengine/metrics.py tradeengine/position_manager.py &> /dev/null; then
        check_pass "Ruff linting passed"
    else
        check_warn "Ruff linting found issues"
    fi
else
    check_warn "Ruff not installed, skipping linting"
fi

if command -v mypy &> /dev/null; then
    if mypy shared/mysql_client.py tradeengine/metrics.py --no-error-summary &> /dev/null; then
        check_pass "Mypy type checking passed"
    else
        check_warn "Mypy found type issues (may be acceptable)"
    fi
else
    check_warn "Mypy not installed, skipping type checking"
fi

echo ""

# Check 6: Verify SQL schema
echo "Checking MySQL schema..."

if grep -q 'position_id VARCHAR(255) UNIQUE NOT NULL' scripts/create_positions_table.sql && \
   grep -q 'position_side ENUM' scripts/create_positions_table.sql && \
   grep -q 'pnl_after_fees DECIMAL' scripts/create_positions_table.sql && \
   grep -q 'exchange VARCHAR' scripts/create_positions_table.sql; then
    check_pass "MySQL schema has all required fields"
else
    check_fail "MySQL schema missing required fields"
fi

if grep -q 'INDEX idx_position_id\|INDEX.*position_id' scripts/create_positions_table.sql && \
   grep -q 'INDEX.*strategy_id' scripts/create_positions_table.sql; then
    check_pass "MySQL schema has required indexes"
else
    check_warn "MySQL schema may be missing some indexes"
fi

echo ""

# Check 7: Verify test coverage
echo "Checking test coverage..."

if [ -f "tests/test_position_tracking.py" ]; then
    if grep -q 'test_create_position_record' tests/test_position_tracking.py && \
       grep -q 'test_position_side_determination' tests/test_position_tracking.py && \
       grep -q 'test_close_position_record' tests/test_position_tracking.py; then
        check_pass "Position tracking tests cover key scenarios"
    else
        check_warn "Test coverage may be incomplete"
    fi
fi

echo ""

# Summary
echo "============================================"
echo "Verification Summary"
echo "============================================"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    echo ""
    echo "The hedge mode implementation is ready for deployment."
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠ $WARNINGS warning(s) found${NC}"
    echo ""
    echo "The implementation looks good but has some warnings."
    echo "Review the warnings above before deploying."
    exit 0
else
    echo -e "${RED}✗ $ERRORS error(s) found${NC}"
    if [ $WARNINGS -gt 0 ]; then
        echo -e "${YELLOW}⚠ $WARNINGS warning(s) found${NC}"
    fi
    echo ""
    echo "Please fix the errors before deploying."
    exit 1
fi
