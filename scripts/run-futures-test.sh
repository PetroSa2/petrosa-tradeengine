#!/bin/bash
"""
Run Binance Futures Testnet Test on Pod

This script runs the Binance Futures testnet test on your Kubernetes pod.
"""

set -e

echo "🚀 Running Binance Futures Testnet Test on Pod"
echo "================================================"

# Check if we're in a pod environment
if [ -z "$KUBECONFIG" ]; then
    echo "⚠️  KUBECONFIG not set, using default"
fi

# Get pod name
POD_NAME=$(kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$POD_NAME" ]; then
    echo "❌ No petrosa-tradeengine pod found"
    echo "Available pods:"
    kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps
    exit 1
fi

echo "📦 Found pod: $POD_NAME"

# Copy test script to pod
echo "📋 Copying test script to pod..."
kubectl --kubeconfig=k8s/kubeconfig.yaml cp scripts/test-binance-futures-testnet.py petrosa-apps/$POD_NAME:/tmp/test-binance-futures-testnet.py

# Run the test
echo "🧪 Running Binance Futures testnet test..."
kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD_NAME -- python3 /tmp/test-binance-futures-testnet.py

echo ""
echo "✅ Test completed!"
echo ""
echo "To run the test again:"
echo "  kubectl --kubeconfig=k8s/kubeconfig.yaml exec -n petrosa-apps $POD_NAME -- python3 /tmp/test-binance-futures-testnet.py"
