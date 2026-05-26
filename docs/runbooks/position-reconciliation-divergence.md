# Runbook: Position-State Reconciliation Divergence (FR65)

**Alert:** `tradeengine_position_reconciliation_alert == 1`
**Verdict metric:** `tradeengine_position_reconciliation_evaluator_verdict == 1`
**FR refs:** FR65 (reconciliation), FR21 (execution evaluator), FR66 category e (alert)

---

## Overview

TradeEngine's `PositionReconciler` queries Binance Futures `/fapi/v2/positionRisk` every
60 s (configurable via `POSITION_RECONCILIATION_INTERVAL_SECONDS`) and compares the result
against the in-memory position tracker.  When the two disagree, the alert fires and
`evaluator.execution.verdict` is set to `unhealthy`.

This is a **read-only** detector — it does not close, re-open, or modify any positions.

---

## Divergence categories

| Category | Meaning | Typical cause |
|---|---|---|
| `untracked` | Binance has a non-zero position; local tracker is empty | Crash-loop wiped the position tracker (#402) or a manual trade was placed outside TradeEngine |
| `ghost` | Local tracker shows an open position; Binance shows zero | Position was closed externally (liquidation, manual close, TP hit) but TradeEngine was not notified |
| `mutation` | Both sides agree a position exists but the quantity differs | Partial fill race condition, rounding, or external size change |

---

## Triage steps

### 1 — Confirm the alert is active

```bash
# Via Prometheus / Grafana
tradeengine_position_reconciliation_alert

# Via logs (last 50 reconciliation warnings)
kubectl logs -n petrosa-apps deploy/petrosa-tradeengine --since=5m \
  | grep "PositionReconciler"
```

### 2 — Identify which positions are diverging

```bash
# Structured log output includes symbol, side, category, and detail
kubectl logs -n petrosa-apps deploy/petrosa-tradeengine --since=10m \
  | grep "divergence(s)"
```

The log line format is:
```
PositionReconciler: N divergence(s) — evaluator.execution.verdict=unhealthy. <category>:<symbol>:<side>; ...
```

### 3 — Compare Binance vs local state

```bash
# Binance live positions
curl -s http://localhost:8000/positions | jq .

# Check Binance directly (requires API key / testnet)
# GET /fapi/v2/positionRisk
```

---

## Remediation by category

### `untracked` — Binance has position, local is empty

1. Verify whether the position is **legitimate** (opened by this TradeEngine instance) or
   **external** (manual trade, another bot, leftover from a crash).
2. If **external/manual**: close it manually on Binance Futures UI or via the Binance API,
   then confirm the alert clears on the next reconciliation cycle (≤ 60 s).
3. If **crash-loop artifact**: after fixing the underlying crash (see #402 runbook), the
   position tracker should re-populate from Data Manager on startup.  Confirm the position
   appears in `/positions` after restart.

### `ghost` — local has position, Binance shows nothing

1. Verify whether the position was **legitimately closed** (TP/SL hit, liquidation) while
   TradeEngine was offline or the fill event was missed.
2. Call `DELETE /positions/{symbol}` (if available) or restart TradeEngine — the tracker
   will reload from Data Manager which should reflect the closed state.
3. If Data Manager is also stale, manually update the DB record to `status=closed`.

### `mutation` — size mismatch

1. Check if a **partial fill** is in flight (`GET /orders?status=partially_filled`).
   A mutation that resolves within 1–2 reconciliation cycles is normal for partial fills.
2. If persistent (> 5 minutes): inspect the position record in Data Manager and compare
   with the Binance positionRisk `positionAmt`.  Identify which side is wrong and update
   accordingly.

---

## Escalation

If the divergence persists beyond **15 minutes** and cannot be explained by a known
partial fill or external manual trade, escalate to the on-call engineer and consider
halting new signal intake:

```bash
# Pause CIO signal processing (prevents new positions opening on stale state)
curl -X POST http://localhost:8000/config/pause
```

---

## Related issues / references

- [#409](https://github.com/PetroSa2/petrosa-tradeengine/issues/409) — implementing ticket
- [#402](https://github.com/PetroSa2/petrosa-tradeengine/issues/402) — NATS consumer crash loop that wipes the position tracker
- [#404](https://github.com/PetroSa2/petrosa-tradeengine/issues/404) — portfolio_value bug (fixed in PR #406)
- FR65, FR21, FR66 — PRD contract references

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `POSITION_RECONCILIATION_ENABLED` | `true` | Set `false` to disable (e.g. pure simulation runs) |
| `POSITION_RECONCILIATION_INTERVAL_SECONDS` | `60` | Cadence in seconds |

The reconciler is automatically **disabled** when `SIMULATION_ENABLED=true` (no real
Binance positions to reconcile against in simulation mode).
