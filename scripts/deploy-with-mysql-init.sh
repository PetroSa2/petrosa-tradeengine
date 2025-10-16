#!/bin/bash
#
# Deploy Petrosa TradeEngine with MySQL Schema Initialization
#
# This script ensures MySQL schema is created before deploying the main application
#

set -e

KUBECONFIG_PATH="${KUBECONFIG:-k8s/kubeconfig.yaml}"
NAMESPACE="petrosa-apps"

echo "============================================"
echo "Petrosa TradeEngine Deployment with MySQL Init"
echo "============================================"
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "ERROR: kubectl is not installed"
    exit 1
fi

# Check if kubeconfig exists
if [ ! -f "$KUBECONFIG_PATH" ]; then
    echo "ERROR: kubeconfig not found at $KUBECONFIG_PATH"
    exit 1
fi

echo "Using kubeconfig: $KUBECONFIG_PATH"
echo "Namespace: $NAMESPACE"
echo ""

# Check cluster connectivity
echo "Checking cluster connectivity..."
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" cluster-info &> /dev/null; then
    echo "ERROR: Cannot connect to Kubernetes cluster"
    exit 1
fi
echo "✓ Cluster connectivity OK"
echo ""

# Check if namespace exists
echo "Checking namespace..."
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" get namespace "$NAMESPACE" &> /dev/null; then
    echo "ERROR: Namespace $NAMESPACE does not exist"
    exit 1
fi
echo "✓ Namespace exists"
echo ""

# Check if secrets exist
echo "Checking secrets..."
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" get secret petrosa-sensitive-credentials -n "$NAMESPACE" &> /dev/null; then
    echo "ERROR: Secret petrosa-sensitive-credentials does not exist"
    exit 1
fi

# Verify MYSQL_URI exists in secret
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" get secret petrosa-sensitive-credentials -n "$NAMESPACE" -o jsonpath='{.data.MYSQL_URI}' | base64 -d > /dev/null 2>&1; then
    echo "ERROR: MYSQL_URI not found in petrosa-sensitive-credentials secret"
    exit 1
fi
echo "✓ Secrets configured correctly"
echo ""

# Step 1: Run MySQL schema initialization job
echo "============================================"
echo "Step 1: MySQL Schema Initialization"
echo "============================================"
echo ""

# Delete old job if exists
if kubectl --kubeconfig="$KUBECONFIG_PATH" get job petrosa-tradeengine-mysql-schema -n "$NAMESPACE" &> /dev/null; then
    echo "Deleting previous MySQL schema job..."
    kubectl --kubeconfig="$KUBECONFIG_PATH" delete job petrosa-tradeengine-mysql-schema -n "$NAMESPACE" --wait=true
fi

echo "Applying MySQL schema job..."
kubectl --kubeconfig="$KUBECONFIG_PATH" apply -f k8s/mysql-schema-job.yaml

echo "Waiting for MySQL schema job to complete..."
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" wait --for=condition=complete --timeout=300s job/petrosa-tradeengine-mysql-schema -n "$NAMESPACE"; then
    echo ""
    echo "ERROR: MySQL schema job failed or timed out"
    echo ""
    echo "Job logs:"
    kubectl --kubeconfig="$KUBECONFIG_PATH" logs -n "$NAMESPACE" job/petrosa-tradeengine-mysql-schema --tail=100
    exit 1
fi

echo ""
echo "✓ MySQL schema initialized successfully"
echo ""
echo "Schema job logs:"
kubectl --kubeconfig="$KUBECONFIG_PATH" logs -n "$NAMESPACE" job/petrosa-tradeengine-mysql-schema --tail=50
echo ""

# Step 2: Deploy main application
echo "============================================"
echo "Step 2: Deploying TradeEngine Application"
echo "============================================"
echo ""

echo "Applying Kubernetes manifests..."
kubectl --kubeconfig="$KUBECONFIG_PATH" apply -f k8s/deployment.yaml
kubectl --kubeconfig="$KUBECONFIG_PATH" apply -f k8s/service.yaml
kubectl --kubeconfig="$KUBECONFIG_PATH" apply -f k8s/hpa.yaml
kubectl --kubeconfig="$KUBECONFIG_PATH" apply -f k8s/ingress.yaml

echo ""
echo "Waiting for deployment to be ready..."
if ! kubectl --kubeconfig="$KUBECONFIG_PATH" rollout status deployment/petrosa-tradeengine -n "$NAMESPACE" --timeout=300s; then
    echo "ERROR: Deployment failed or timed out"
    exit 1
fi

echo ""
echo "✓ Deployment successful"
echo ""

# Step 3: Verify deployment
echo "============================================"
echo "Step 3: Deployment Verification"
echo "============================================"
echo ""

echo "Deployment status:"
kubectl --kubeconfig="$KUBECONFIG_PATH" get deployment petrosa-tradeengine -n "$NAMESPACE"
echo ""

echo "Pods:"
kubectl --kubeconfig="$KUBECONFIG_PATH" get pods -n "$NAMESPACE" -l app=petrosa-tradeengine
echo ""

echo "Services:"
kubectl --kubeconfig="$KUBECONFIG_PATH" get svc -n "$NAMESPACE" -l app=petrosa-tradeengine
echo ""

# Check if pods are running
RUNNING_PODS=$(kubectl --kubeconfig="$KUBECONFIG_PATH" get pods -n "$NAMESPACE" -l app=petrosa-tradeengine --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)

if [ "$RUNNING_PODS" -eq 0 ]; then
    echo "WARNING: No pods are running yet"
    echo ""
    echo "Recent pod events:"
    kubectl --kubeconfig="$KUBECONFIG_PATH" get events -n "$NAMESPACE" --sort-by='.lastTimestamp' --field-selector involvedObject.kind=Pod | tail -20
else
    echo "✓ $RUNNING_PODS pod(s) running"
fi

echo ""
echo "============================================"
echo "✓ Deployment Complete"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Check logs: kubectl --kubeconfig=$KUBECONFIG_PATH logs -n $NAMESPACE -l app=petrosa-tradeengine --tail=100"
echo "  2. Verify metrics: kubectl --kubeconfig=$KUBECONFIG_PATH port-forward -n $NAMESPACE svc/petrosa-tradeengine 8000:80"
echo "  3. Check position metrics: curl http://localhost:8000/metrics | grep position"
echo "  4. Verify hedge mode: kubectl --kubeconfig=$KUBECONFIG_PATH exec -n $NAMESPACE -it deployment/petrosa-tradeengine -- python scripts/verify_hedge_mode.py"
echo ""
