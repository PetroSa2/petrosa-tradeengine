# Runbook: Unhedged Position Detected

**Alert:** `UnhedgedPositionDetected` (Grafana Cloud)
**Metric:** `tradeengine_position_reconciliation_divergences_total{category="unhedged"} > 0`
**Severity:** `critical`
**Origin:** AC5 / RC#5 of [#424](https://github.com/PetroSa2/petrosa-tradeengine/issues/424) (2026-05-30 OCO orphan-legs incident)

---

## What this alert means

A live, non-zero position exists on Binance Futures for which TradeEngine cannot find both:

- a reduceOnly **STOP**-shaped order (stop loss), AND
- a reduceOnly **TAKE_PROFIT**-shaped order (take profit)

on the matching `(symbol, positionSide)`. The position is running **unprotected** — a routine adverse move will hit the entry-side margin without firing any pre-placed exit.

On 2026-05-30 this exact state covered **11 of 12** open positions for **2+ hours** with no alert firing. The reconciler now flags it within 60 s.

---

## Why it triggers

- **OCO placement failed during entry**, and the atomic-rollback path (AC2 / [#426](https://github.com/PetroSa2/petrosa-tradeengine/pull/434)) didn't close the position — historically blocked when `order.position_id` was absent or `filled_qty<=0`.
- **stops-health stored a price string in `sl_order_id` / `tp_order_id`** (AC3 / [#427](https://github.com/PetroSa2/petrosa-tradeengine/pull/435)) — the position looked healthy locally while Binance held nothing.
- **TP/SL `cancel` succeeded but their replacement wasn't placed** — e.g. operator UI action, mass cancel, or a deploy/restart between cancel and place.
- **Algo-order limit** hit on the symbol (10 open algos max per Binance Futures) — new SL/TP could not be placed and the existing pair was cancelled.

---

## Triage steps

### 1 — Confirm the alert + identify affected symbols

```bash
# Grafana / Prometheus
sum(tradeengine_position_reconciliation_divergences_total{category="unhedged"}) by (symbol)

# Reconciler log lines
kubectl --kubeconfig=petrosa_k8s/k8s/kubeconfig.yaml --insecure-skip-tls-verify=true \
  logs -n petrosa-apps deploy/petrosa-tradeengine --since=10m \
  | grep -E "unhedged|PositionReconciler"
```

The log line carries `symbol`, `side`, `sl_present`, `tp_present`.

### 2 — Cross-check against Binance directly

```bash
# Open positions
curl -s http://localhost:8000/positions | jq '.[] | select(.amount != 0)'

# Open algo orders (Binance ground truth)
curl -s http://localhost:8000/orders/algo | jq '.[] | {symbol, type, positionSide, reduceOnly, closePosition}'

# Or via the data-manager dashboard endpoint
curl -s http://localhost:8001/api/dashboard/positions | jq .
```

You should be able to point to each unhedged `(symbol, side)` and confirm:

- there IS a non-zero `positionAmt` on Binance, AND
- there is NOT both a reduceOnly STOP order AND a reduceOnly TAKE_PROFIT order on the same `positionSide` (or `BOTH` for one-way mode).

### 3 — Decide: place stops, or close the position

If the position is small / recent / strategy still has conviction → **place protective stops manually** (via the Binance UI or `POST /orders/oco`).

If the position has drifted far from entry, the strategy has closed elsewhere, or you cannot reconstruct intended stops → **close the position via the remediation script** (below).

---

## Remediation — one-shot script

`scripts/close_unhedged_positions.py` lists unhedged positions (dry-run) and, with `--commit`, issues `MARKET reduceOnly` close orders against them.

### Dry-run (default — always safe)

```bash
cd petrosa-tradeengine
.venv/bin/python scripts/close_unhedged_positions.py
```

Prints a JSON line per unhedged position:

```json
{"symbol":"BCHUSDT","positionSide":"LONG","positionAmt":0.5,"sl_present":false,"tp_present":false,"would_close":true}
```

Exit code 0 if any are listed, 1 otherwise — safe to pipe into operator tooling.

### Commit (live close)

```bash
.venv/bin/python scripts/close_unhedged_positions.py --commit
```

Requires `BINANCE_API_KEY` + `BINANCE_API_SECRET` in env. Issues `MARKET reduceOnly` orders for each unhedged `(symbol, positionSide)`; prints the order ID returned by Binance.

Skips any position that already has both reduceOnly SL+TP — the dry-run output reflects the moment the script ran, not the operator's view; re-run with `--commit` if state changed in between.

### Limiting scope

```bash
# Only close ETHUSDT
.venv/bin/python scripts/close_unhedged_positions.py --commit --symbol ETHUSDT

# Only close positions older than 1 hour
.venv/bin/python scripts/close_unhedged_positions.py --commit --min-age-seconds 3600
```

---

## Post-fix verification

After AC1-AC5 land, re-run the incident reproduction tests:

```bash
cd petrosa_k8s
.venv/bin/python -m pytest _bmad-output/incidents/2026-05-30/reproduction_test.py -v
```

All four (`test_h1` through `test_h4`) MUST pass. If any still fail, file a fresh leaf — do not modify the reproduction file.

---

## Why this happened — engineering background

| Root cause | AC | Ticket | Status |
|---|---|---|---|
| RC#1 — partial OCO left surviving leg uncancelled | AC1 | [#425](https://github.com/PetroSa2/petrosa-tradeengine/pull/433) | ✅ shipped |
| RC#2 — atomic-rollback early-returned without closing | AC2 | [#426](https://github.com/PetroSa2/petrosa-tradeengine/pull/434) | shipped in PR |
| RC#3 — adjuster clipped SL to filter boundary (-2021) | AC4 | [#428](https://github.com/PetroSa2/petrosa-tradeengine/pull/436) | shipped in PR |
| RC#4 — stops-health stored price strings as IDs | AC3 | [#427](https://github.com/PetroSa2/petrosa-tradeengine/pull/435) | shipped in PR |
| RC#5 — reconciler didn't check position↔SL+TP parity | AC5 | [#429](https://github.com/PetroSa2/petrosa-tradeengine/pull/437) | shipped in PR |
| RC#6 — no runbook for the unhedged state | AC6 | [#430](https://github.com/PetroSa2/petrosa-tradeengine/issues/430) | this document |

---

## Owners

- **Tradeengine subsystem:** @yurisa2
- **Observability rule:** `petrosa_k8s/observability/alert-rules/tradeengine-business-alerts.yaml` (uid `tradeengine-unhedged-position-detected`)
