#!/usr/bin/env bash
# C3: Flip naked-position remediation mode from dry_run → arm_or_flatten.
#
# Run AFTER the new image (with C1/C2) is deployed. The mode flip must be
# atomic with the image — do NOT flip before the deploy or the remediator
# will attempt re-arms with the old 2% fallback (un-armable against the
# 6% safety floor) and degrade to flatten-everything.
#
# Usage:
#   bash .github/deployments/flip-remediation-mode.sh [--dry-run]
#
# Requires: kubectl with petrosa kubeconfig, TE_NAKED_POSITION_REMEDIATION_MODE
#           must currently be "dry_run".
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

DEPLOY="petrosa-tradeengine"
NAMESPACE="petrosa-apps"
KUBECTL="kubectl --kubeconfig=petrosa_k8s/k8s/kubeconfig.yaml --insecure-skip-tls-verify=true"

CURRENT=$($KUBECTL get deploy "$DEPLOY" -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="TE_NAKED_POSITION_REMEDIATION_MODE")].value}')

echo "Current TE_NAKED_POSITION_REMEDIATION_MODE: $CURRENT"

if [[ "$CURRENT" != "dry_run" ]]; then
  echo "ERROR: expected 'dry_run', got '$CURRENT'. Aborting to avoid double-flip."
  exit 1
fi

if $DRY_RUN; then
  echo "[dry-run] Would patch TE_NAKED_POSITION_REMEDIATION_MODE → arm_or_flatten"
  exit 0
fi

$KUBECTL set env deploy/"$DEPLOY" -n "$NAMESPACE" \
  TE_NAKED_POSITION_REMEDIATION_MODE=arm_or_flatten

echo "✅ TE_NAKED_POSITION_REMEDIATION_MODE set to arm_or_flatten"
echo "   Verify: kubectl -n $NAMESPACE logs deploy/$DEPLOY --since=5m | grep NakedPositionRemediator"
echo "   Watch:  tradeengine_naked_position_rearmed_total{outcome='armed'} should increment"
