# Runbook — algo-order accumulation

**Linked alerts (Grafana Cloud, defined in `petrosa_k8s/observability/alert-rules/tradeengine-business-alerts.yaml`):**

- `tradeengine-algo-order-accumulation` (critical, fires at `tradeengine_algo_orders_open > 4` for 2m).
- `tradeengine-algo-order-limit-approaching` (warning, fires at `>= 8` for 1m).

Both alerts protect against the per-symbol Binance limit of 10 conditional algo orders. Trading is paralysed on the affected symbol the moment the 10 limit is hit.

## What "normal" looks like

For an active position on `<symbol>`, the engine maintains **exactly 2** conditional algo orders:

- 1 STOP_LOSS (SL)
- 1 TAKE_PROFIT (TP) — paired via OCO

Any count above 2 per active position is anomalous. Above 4 means orphans have started accumulating.

## Quick triage (≤ 5 min)

1. **Confirm the metric is real**, not a Grafana Cloud display glitch:
   ```bash
   eval $(scripts/agent-init.sh)
   kubectl --kubeconfig="$KUBECONFIG" -n petrosa-apps logs deploy/petrosa-tradeengine --tail=200 \
     | grep -iE 'algo[_-]order|oco[_-]pair'
   ```
2. **List the actual open algo orders on Binance** for the firing symbol:
   ```bash
   curl -sS -X GET "https://fapi.binance.com/fapi/v1/openAlgoOrders?symbol=$SYMBOL&timestamp=$(date +%s%3N)" \
     -H "X-MBX-APIKEY: $BINANCE_API_KEY"
   ```
   (Use Binance testnet base if the env is staging.)
3. **Cross-check active positions**:
   ```bash
   curl -sS -X GET "https://fapi.binance.com/fapi/v2/positionRisk?symbol=$SYMBOL" \
     -H "X-MBX-APIKEY: $BINANCE_API_KEY"
   ```
   If `positionAmt == 0` and Binance still has algo orders open: those are orphans, cancel them.

## Root-cause hypotheses (most → least likely)

1. **NATS queue group absent.** Both `petrosa-tradeengine` pods process the same signal, each placing an OCO pair → 2× the expected count per signal.
   - Check: `kubectl get pods -n petrosa-apps -l app=petrosa-tradeengine`; expect 1 or NATS queue group `tradeengine-orders` active.
2. **OCO dedup guard regressed.** Multiple strategies (e.g. `realtime-strategies` + `bot-ta-analysis`) emit signals on the same symbol within seconds; the dedup window was bypassed.
   - Check `tradeengine` logs filtered to `oco_dedup_pass=true` for a burst of true → likely cause.
3. **`active_oco_pairs` in-memory state lost on pod restart.** A restart between SL and TP placement → engine forgets and may place a new pair on the next signal, leaving the first SL/TP orphaned.
   - Check pod restart timestamps in the past 24h.
4. **Binance auto-cancel didn't fire** when position closed (rare). Engine should clean up via the orderbook callback; this can be confirmed in the position close-event logs.

## Remediation

1. **Cancel orphan algo orders** on the affected symbol:
   ```bash
   # For each orphan order_id returned by /openAlgoOrders that has no matching position:
   curl -sS -X DELETE "https://fapi.binance.com/fapi/v1/algoOrder?symbol=$SYMBOL&orderId=$OID&timestamp=$(date +%s%3N)" \
     -H "X-MBX-APIKEY: $BINANCE_API_KEY"
   ```
2. **If queue-group hypothesis confirms:** scale tradeengine to a single replica until queue-group config is restored (or rotate config + restart).
3. **If dedup-guard regression:** open an immediate ticket against `petrosa-tradeengine` and gate the OCO placement behind a feature flag while investigating.

## Backlog reference

- Originally tracked by #352 AC-7 in `petrosa_k8s`. The PrometheusRule manifests were removed in #536 (never fired anyway — local minimal-prometheus had zero AlertManagers). #539 re-instates them as Grafana Cloud Alerts. This runbook is the AC4 link.
- Related: #372 (TradeEngine TP fallback) — overlapping order-management surface.

_Last verified clean: 2026-05-18T00:12Z (#559 verification run)._

<!-- #559 verification re-trigger 1779063822 -->
