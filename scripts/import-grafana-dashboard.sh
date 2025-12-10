#!/usr/bin/env bash
#
# Import Grafana Dashboard for Trade Execution Monitoring
#
# This script imports the business metrics dashboard into Grafana Cloud.
#
# Usage:
#   ./scripts/import-grafana-dashboard.sh [--overwrite]
#
# Requirements:
#   - jq (for JSON processing)
#   - curl (for API calls)
#   - Grafana API token (from GRAFANA_API_TOKEN env var or kubectl secret)
#   - Grafana URL (from GRAFANA_URL env var or kubectl secret)
#
# The script will:
#   1. Validate the dashboard JSON file
#   2. Get Grafana credentials from environment or Kubernetes secrets
#   3. Import the dashboard via Grafana API
#   4. Display the dashboard URL for access
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
error() {
  echo -e "${RED}❌ ERROR: $1${NC}" >&2
  exit 1
}

success() {
  echo -e "${GREEN}✓ $1${NC}"
}

warning() {
  echo -e "${YELLOW}⚠ $1${NC}"
}

info() {
  echo -e "${BLUE}ℹ $1${NC}"
}

# Check requirements
command -v jq >/dev/null 2>&1 || error "jq is required but not installed. Install with: brew install jq"
command -v curl >/dev/null 2>&1 || error "curl is required but not installed"

# Parse arguments
OVERWRITE=false
if [ "${1:-}" = "--overwrite" ]; then
  OVERWRITE=true
  info "Overwrite mode enabled"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_FILE="$REPO_ROOT/docs/grafana-trade-execution-dashboard.json"

# Check if dashboard file exists
if [ ! -f "$DASHBOARD_FILE" ]; then
  error "Dashboard file not found: $DASHBOARD_FILE"
fi

# Validate JSON
if ! jq empty "$DASHBOARD_FILE" 2>/dev/null; then
  error "Invalid JSON in dashboard file: $DASHBOARD_FILE"
fi

success "Dashboard file validated: $DASHBOARD_FILE"

# Get Grafana credentials
if [ -z "${GRAFANA_URL:-}" ]; then
  info "GRAFANA_URL not set, attempting to get from kubectl secret..."

  # Try to get from Kubernetes secret
  if command -v kubectl >/dev/null 2>&1; then
    KUBECONFIG_PATH="$REPO_ROOT/../petrosa_k8s/k8s/kubeconfig.yaml"
    if [ -f "$KUBECONFIG_PATH" ]; then
      GRAFANA_INSTANCE_ID=$(kubectl --kubeconfig="$KUBECONFIG_PATH" get secret petrosa-sensitive-credentials \
        -n petrosa-apps \
        -o jsonpath='{.data.GRAFANA_CLOUD_INSTANCE_ID}' 2>/dev/null | base64 --decode || echo "")

      if [ -n "$GRAFANA_INSTANCE_ID" ]; then
        GRAFANA_URL="https://${GRAFANA_INSTANCE_ID}.grafana.net"
        success "Grafana URL from secret: $GRAFANA_URL"
      else
        # Try alternative: use yurisa2 as default
        GRAFANA_URL="https://yurisa2.grafana.net"
        warning "Could not get instance ID from secret, using default: $GRAFANA_URL"
      fi
    else
      GRAFANA_URL="https://yurisa2.grafana.net"
      warning "kubeconfig not found, using default: $GRAFANA_URL"
    fi
  else
    GRAFANA_URL="https://yurisa2.grafana.net"
    warning "kubectl not available, using default: $GRAFANA_URL"
  fi
fi

if [ -z "${GRAFANA_API_TOKEN:-}" ]; then
  info "GRAFANA_API_TOKEN not set, attempting to get from kubectl secret..."

  # Try to get from Kubernetes secret
  if command -v kubectl >/dev/null 2>&1; then
    KUBECONFIG_PATH="$REPO_ROOT/../petrosa_k8s/k8s/kubeconfig.yaml"
    if [ -f "$KUBECONFIG_PATH" ]; then
      GRAFANA_API_TOKEN=$(kubectl --kubeconfig="$KUBECONFIG_PATH" get secret petrosa-sensitive-credentials \
        -n petrosa-apps \
        -o jsonpath='{.data.GRAFANA_CLOUD_API_KEY}' 2>/dev/null | base64 --decode || echo "")

      if [ -n "$GRAFANA_API_TOKEN" ]; then
        success "Grafana API token retrieved from secret"
      else
        error "GRAFANA_API_TOKEN not set and could not get from secret.

Please set:
  export GRAFANA_API_TOKEN='your-api-token'

Or create API token in Grafana:
  1. Go to https://yurisa2.grafana.net
  2. Navigate to Configuration → API Keys
  3. Create new key with Editor role
  4. Copy token and export as env var"
      fi
    else
      error "GRAFANA_API_TOKEN not set and kubeconfig not found.

Please set:
  export GRAFANA_API_TOKEN='your-api-token'"
    fi
  else
    error "GRAFANA_API_TOKEN not set and kubectl not available.

Please set:
  export GRAFANA_API_TOKEN='your-api-token'"
  fi
fi

info "Grafana URL: $GRAFANA_URL"

# Prepare import payload
info "Preparing import payload..."

TEMP_PAYLOAD=$(mktemp)
trap "rm -f $TEMP_PAYLOAD" EXIT

# Wrap dashboard in API format
# The dashboard JSON already has a "dashboard" wrapper, so we need to extract it
jq -n \
  --slurpfile dashboard "$DASHBOARD_FILE" \
  --argjson overwrite "$OVERWRITE" \
  '{
    dashboard: $dashboard[0].dashboard,
    overwrite: $overwrite,
    folderId: 0,
    message: "Imported via script - Trade Execution Monitoring Dashboard"
  }' > "$TEMP_PAYLOAD"

success "Import payload prepared"

# Import dashboard
info "Importing dashboard to Grafana Cloud..."

HTTP_RESPONSE=$(mktemp)
trap "rm -f $HTTP_RESPONSE" EXIT

HTTP_CODE=$(curl -s -w "%{http_code}" \
  -X POST "$GRAFANA_URL/api/dashboards/db" \
  -H "Authorization: Bearer $GRAFANA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @"$TEMP_PAYLOAD" \
  -o "$HTTP_RESPONSE")

if [ "$HTTP_CODE" = "200" ]; then
  success "Dashboard imported successfully!"

  # Extract dashboard details
  DASHBOARD_UID=$(jq -r '.uid' "$HTTP_RESPONSE")
  DASHBOARD_URL=$(jq -r '.url' "$HTTP_RESPONSE")
  DASHBOARD_SLUG=$(jq -r '.slug' "$HTTP_RESPONSE")

  echo ""
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${GREEN}Dashboard Import Successful${NC}"
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
  echo "Dashboard UID: $DASHBOARD_UID"
  echo "Dashboard Slug: $DASHBOARD_SLUG"
  echo "Dashboard URL: $GRAFANA_URL$DASHBOARD_URL"
  echo ""
  echo -e "${BLUE}Next steps:${NC}"
  echo "1. Open dashboard: $GRAFANA_URL$DASHBOARD_URL"
  echo "2. Verify all 9 panels display data (no 'No Data' errors)"
  echo "3. Test time range selection (default: Last 6h)"
  echo "4. Verify auto-refresh is working (10s interval)"
  echo "5. Configure Prometheus data source if needed"
  echo "6. Share URL in team documentation"
  echo ""

  # Save URL for later use
  echo "$GRAFANA_URL$DASHBOARD_URL" > /tmp/grafana-dashboard-url.txt
  success "Dashboard URL saved to: /tmp/grafana-dashboard-url.txt"

elif [ "$HTTP_CODE" = "401" ]; then
  error "Authentication failed. Check GRAFANA_API_TOKEN is valid."
elif [ "$HTTP_CODE" = "403" ]; then
  error "Permission denied. Ensure API token has Editor role."
elif [ "$HTTP_CODE" = "412" ]; then
  warning "Dashboard already exists. Use --overwrite flag to replace it."
  echo ""
  echo "Run:"
  echo "  $0 --overwrite"
  exit 1
else
  error "Import failed with HTTP $HTTP_CODE

Response:
$(cat "$HTTP_RESPONSE")"
fi
