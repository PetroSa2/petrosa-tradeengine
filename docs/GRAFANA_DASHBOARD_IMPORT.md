# Grafana Dashboard Import Guide

## Overview

This guide explains how to import the Trade Execution Monitoring dashboard into Grafana Cloud. The dashboard provides real-time visibility into order execution performance, risk management, and trading metrics.

## Dashboard Details

**File**: `docs/grafana-trade-execution-dashboard.json`
**Panels**: 9 panels covering:
1. Order Execution Rate (by type)
2. Order Execution Latency (histogram, p50/p95/p99)
3. Order Failure Rate
4. Risk Rejections (position limits & daily loss)
5. Risk Check Pass Rate
6. Current Position Sizes (by symbol)
7. Total Position Value
8. Realized vs Unrealized PnL
9. Daily PnL Trend

**Default Settings**:
- Time Range: Last 6 hours
- Auto-refresh: 10 seconds
- Data Source: Prometheus (must be configured)

## Prerequisites

- Access to Grafana Cloud instance (`https://yurisa2.grafana.net`)
- Grafana API token with Editor role (or Admin)
- `jq` installed (`brew install jq` on macOS)
- `curl` installed (usually pre-installed)

## Import Methods

### Method 1: Automated Script (Recommended)

The easiest way to import the dashboard is using the provided script:

```bash
# From the repository root
./scripts/import-grafana-dashboard.sh
```

**What the script does**:
1. Validates the dashboard JSON file
2. Automatically retrieves Grafana credentials from Kubernetes secrets (if available)
3. Imports the dashboard via Grafana API
4. Displays the dashboard URL for access

**If dashboard already exists**:
```bash
# Use --overwrite flag to replace existing dashboard
./scripts/import-grafana-dashboard.sh --overwrite
```

**Manual credential setup** (if secrets not available):
```bash
export GRAFANA_URL="https://yurisa2.grafana.net"
export GRAFANA_API_TOKEN="your-api-token-here"
./scripts/import-grafana-dashboard.sh
```

### Method 2: Manual Import via Grafana UI

If you prefer visual confirmation or don't have API access:

1. **Access Grafana Cloud**:
   - Go to: https://yurisa2.grafana.net
   - Log in with your credentials

2. **Navigate to Import**:
   - Click **Dashboards** in left sidebar
   - Click **New** → **Import**
   - Click **Upload JSON file**

3. **Select Dashboard File**:
   - Choose: `docs/grafana-trade-execution-dashboard.json`

4. **Configure Import Settings**:
   - **Name**: "Trade Execution Monitoring" (or keep default)
   - **Folder**: Select "Petrosa" or create new folder
   - **UID**: Leave blank (auto-generate) or use custom
   - **Data Source**: Select your Prometheus data source
     - Ensure it matches your OTEL metrics source
     - Should point to Grafana Cloud Prometheus

5. **Complete Import**:
   - Click **Import**
   - Dashboard will open automatically

6. **Verify Dashboard**:
   - Check each panel for data (no "No Data" errors)
   - Verify time range (default: Last 6h)
   - Test auto-refresh (10s interval)
   - Confirm all 9 panels are visible

### Method 3: Grafana API (Direct)

For automation or CI/CD integration:

```bash
# Set credentials
export GRAFANA_URL="https://yurisa2.grafana.net"
export GRAFANA_API_TOKEN="your-api-token"

# Prepare payload
jq -n \
  --slurpfile dashboard docs/grafana-trade-execution-dashboard.json \
  '{
    dashboard: $dashboard[0].dashboard,
    overwrite: false,
    folderId: 0,
    message: "Imported via API"
  }' > /tmp/dashboard-import.json

# Import
curl -X POST "$GRAFANA_URL/api/dashboards/db" \
  -H "Authorization: Bearer $GRAFANA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/dashboard-import.json
```

## Getting Grafana API Token

1. **Navigate to API Keys**:
   - Go to: https://yurisa2.grafana.net
   - Click **Configuration** (gear icon) → **API Keys**

2. **Create New Key**:
   - Click **Add API key**
   - Name: "Dashboard Import Automation"
   - Role: **Editor** (minimum required for dashboard import)
   - Time to live: Set expiration or leave blank for no expiration

3. **Copy Token**:
   - Copy the generated token immediately (it won't be shown again)
   - Store securely (e.g., in Kubernetes secret)

4. **Add to Kubernetes Secret** (optional):
   ```bash
   kubectl --kubeconfig=../petrosa_k8s/k8s/kubeconfig.yaml \
     create secret generic grafana-api-token \
     --from-literal=token='YOUR_API_TOKEN' \
     -n petrosa-apps \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

## Verifying Dashboard Import

After importing, verify the dashboard works correctly:

### 1. Check All Panels Display Data

Open the dashboard and verify:
- ✅ No "No Data" errors in any panel
- ✅ Metrics appear in time series charts
- ✅ Histograms show latency distributions
- ✅ Counters show execution rates

### 2. Test Time Range Selection

- Select different time ranges: 1h, 6h, 24h, 7d
- Verify data updates correctly
- Check that auto-refresh works (10s interval)

### 3. Verify PromQL Queries

Each panel uses PromQL queries. Verify they return data:
- `rate(tradeengine_orders_executed_by_type_total[5m])`
- `histogram_quantile(0.95, sum(rate(tradeengine_order_execution_latency_seconds_bucket[5m])) by (le, symbol))`
- `rate(tradeengine_order_failures_total[5m])`
- And others (see dashboard JSON for full list)

### 4. Test Dashboard Permissions

- Verify team members can access dashboard (read-only)
- Confirm admins can edit dashboard
- Test sharing functionality

## Troubleshooting

### "No Data" Errors

**Possible causes**:
1. **Prometheus data source not configured**: Ensure Prometheus data source points to Grafana Cloud Prometheus
2. **Metrics not being emitted**: Check that `OTEL_ENABLED=true` in tradeengine deployment
3. **Time range too narrow**: Try expanding time range to see historical data
4. **Metric names don't match**: Verify metric names in dashboard match those in `tradeengine/metrics.py`

**Solutions**:
- Check Prometheus data source configuration in Grafana
- Verify metrics are being exported: `kubectl logs -n petrosa-apps -l app=tradeengine | grep -i metric`
- Review metric definitions in `docs/BUSINESS_METRICS.md`

### Authentication Errors

**401 Unauthorized**:
- Check API token is valid and not expired
- Verify token has Editor role (minimum required)

**403 Forbidden**:
- Ensure API token has Editor or Admin role
- Check token permissions in Grafana UI

### Dashboard Already Exists

**Error**: "Dashboard with the same name already exists"

**Solution**:
```bash
# Use --overwrite flag
./scripts/import-grafana-dashboard.sh --overwrite
```

Or delete existing dashboard in Grafana UI first.

## Dashboard URL

After successful import, the dashboard URL will be displayed and saved to `/tmp/grafana-dashboard-url.txt`.

**Example URL format**:
```
https://yurisa2.grafana.net/d/trade-execution-monitoring/trade-execution-monitoring
```

**Share this URL**:
- Add to team documentation
- Include in runbooks
- Share with stakeholders
- Add to GitHub issue/PR

## Related Documentation

- **Business Metrics**: `docs/BUSINESS_METRICS.md` - Complete metric documentation
- **Metrics Implementation**: `tradeengine/metrics.py` - Metric definitions
- **Dashboard JSON**: `docs/grafana-trade-execution-dashboard.json` - Dashboard source

## Next Steps

After importing the dashboard:

1. ✅ Verify all panels display data
2. ✅ Test time range selection and auto-refresh
3. ✅ Configure dashboard permissions
4. ✅ Share dashboard URL with team
5. ✅ Add URL to runbook or team wiki
6. ✅ Set up alerts based on dashboard metrics (optional)

## Support

If you encounter issues:
1. Check this guide's troubleshooting section
2. Review Grafana API documentation: https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/
3. Verify metrics are being emitted: `kubectl logs -n petrosa-apps -l app=tradeengine`
4. Check Prometheus data source configuration in Grafana
