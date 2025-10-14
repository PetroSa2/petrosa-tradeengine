#!/bin/bash
# Script to help set up Pyroscope authentication token

set -e

KUBECONFIG="${KUBECONFIG:-k8s/kubeconfig.yaml}"

echo "🔥 Pyroscope Token Setup for TradeEngine"
echo "========================================"
echo ""

echo "📋 Steps to get your Pyroscope token:"
echo ""
echo "1. Go to Grafana Cloud: https://grafana.com/orgs/yurisa2/stacks"
echo "2. Click on your stack"
echo "3. Navigate to 'Profiles' or 'Pyroscope' section"
echo "4. Click 'Configure' or 'Send profiles'"
echo "5. Generate or copy your Pyroscope token"
echo ""
echo "You'll need:"
echo "  - User ID (e.g., 123456)"
echo "  - Token (starts with 'glc_')"
echo "  - Endpoint URL (e.g., https://profiles-prod-011.grafana.net)"
echo ""

# Check if token is provided as argument
if [ -z "$1" ]; then
    echo "Usage: $0 <PYROSCOPE_TOKEN>"
    echo ""
    echo "Example:"
    echo "  $0 'glc_your_token_here'"
    echo ""
    echo "Or set GRAFANA_CLOUD_TOKEN environment variable:"
    echo "  export GRAFANA_CLOUD_TOKEN='glc_your_token_here'"
    echo "  $0"
    echo ""

    if [ -n "$GRAFANA_CLOUD_TOKEN" ]; then
        TOKEN="$GRAFANA_CLOUD_TOKEN"
        echo "✅ Using token from GRAFANA_CLOUD_TOKEN environment variable"
    else
        echo "❌ No token provided. Exiting."
        exit 1
    fi
else
    TOKEN="$1"
fi

echo ""
echo "🔐 Updating Kubernetes secret..."

# Update the secret
kubectl --kubeconfig="$KUBECONFIG" get secret petrosa-sensitive-credentials -n petrosa-apps -o json | \
  jq --arg token "$TOKEN" '.data.PYROSCOPE_AUTH_TOKEN = ($token | @base64)' | \
  kubectl --kubeconfig="$KUBECONFIG" apply -f -

if [ $? -eq 0 ]; then
    echo "✅ Pyroscope token added to petrosa-sensitive-credentials"
    echo ""
    echo "🚀 Next steps:"
    echo "  1. Verify the configmap has profiler settings:"
    echo "     kubectl --kubeconfig=$KUBECONFIG get configmap petrosa-common-config -n petrosa-apps -o yaml | grep PYROSCOPE"
    echo ""
    echo "  2. Deploy the changes via CI/CD workflow"
    echo ""
    echo "  3. Check pods for profiler initialization:"
    echo "     kubectl --kubeconfig=$KUBECONFIG logs -n petrosa-apps -l app=petrosa-tradeengine | grep Pyroscope"
    echo ""
    echo "  4. View profiles in Grafana Cloud:"
    echo "     https://yurisa2.grafana.net → Explore → Pyroscope"
    echo ""
else
    echo "❌ Failed to update secret"
    exit 1
fi
