# Runbook: Position-State Reconciliation Divergence (FR65)

**Alert:** `tradeengine_position_reconciliation_alert == 1`
**Verdict metric:** `tradeengine_position_reconciliation_evaluator_verdict == 1`
**FR refs:** FR65 (reconciliation), FR21 (execution evaluator), FR66 category e (alert)

---

## Overview

TradeEngine's `PositionReconciler` queries Binance Futures `/fapi/v2/positionRisk` every
60 s (configurable via `POSITION_RECONCILIATION_INTERVAL_SECONDS`) and compares the result
against the in-memory position tracker.  When the two disagree, the alert fires.

The reconciler itself is a **read-only** detector — it does not close, re-open, or modify
any positions. Two dedicated write-mode remediators act on its output:
`NakedPositionRemediator` (#445, exchange-side arm/flatten for `unhedged`/`malformed_position`)
and `GhostPositionRemediator` (#592, local-journal-only void for `ghost`) — see
"Remediation by category" below.

### Verdict states (#592)

`evaluator.execution.verdict` (`tradeengine_position_reconciliation_verdict_state`) is now
ternary, not binary:

| Verdict | State value | Meaning | Hard-blocks intake? |
|---|---|---|---|
| `healthy` | 0 | No divergences | No |
| `degraded` | 1 | Divergences present, but **all** are `ghost` and/or `raw_journal_count_mismatch` — self-healing, journal-only issues | **No** — the legacy binary gauge `tradeengine_position_reconciliation_evaluator_verdict` is also `0` in this state |
| `unhealthy` | 2 | At least one `untracked`, `mutation`, `unhedged`, or `malformed_position` divergence — real exchange-state risk | Yes — legacy binary gauge is `1` |

The FR66 category-e alert (`tradeengine_position_reconciliation_alert`) still fires at `1`
for **any** divergence (degraded or unhealthy) so this runbook stays in the loop even when
intake isn't hard-blocked.

---

## Divergence categories

| Category | Meaning | Typical cause |
|---|---|---|
| `untracked` | Binance has a non-zero position; local tracker is empty | Crash-loop wiped the position tracker (#402) or a manual trade was placed outside TradeEngine |
| `ghost` | Local tracker shows an open position; Binance shows zero | Position was closed externally (liquidation, manual close, TP hit) but TradeEngine was not notified |
| `mutation` | Both sides agree a position exists but the quantity differs | Partial fill race condition, rounding, or external size change |
| `raw_journal_count_mismatch` | `len(position_manager.positions)` (raw local audit journal) disagrees with `get_positions()` (the exchange-authoritative accessor when `TE_EXCHANGE_TRUTH_STORE_ENABLED=on`) | Stale entries in the raw journal never pruned on close — the class of bug behind [#587](https://github.com/PetroSa2/petrosa-tradeengine/issues/587) (`/state` reported 13 open positions, `/positions` reported 1) |

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

Since #592, this is handled automatically by `GhostPositionRemediator`
(`GHOST_POSITION_REMEDIATION_MODE`, default `void`): the stale
`PositionManager.positions` entry for the `(symbol, side)` is deleted with an
audit record (`audit_logger.log_position(..., status="voided_ghost")`) the **same
cycle** it's detected — it never re-materialises the position on the exchange
(a ghost means Binance has *nothing*; placing an order to match a phantom local
record would create a real, unintended position). The verdict for a ghost-only
cycle is `degraded`, not `unhealthy` (see "Verdict states" above), and normally
clears within 1–2 reconciliation cycles.

Manual steps are now only needed if it does **not** clear automatically:

1. Confirm the remediator is not disabled: `GHOST_POSITION_REMEDIATION_MODE` should be
   `void` (check `dry_run`/`off` overrides).
2. Force an out-of-cycle pass: `curl -X POST http://localhost:8000/admin/reconcile-positions`
   (idempotent — safe to call repeatedly; returns the verdict + divergence list).
3. If it still recurs for the *same* symbol every cycle, the write is failing silently or the
   `ExchangeTruthStore` itself is stale (check `tradeengine_exchange_truth_store_stale_seconds`
   — a large value means the WS stream + REST self-heal are both not refreshing it; see #592).
4. Last resort: restart TradeEngine — the tracker reloads from Data Manager / the exchange.

### `mutation` — size mismatch

1. Check if a **partial fill** is in flight (`GET /orders?status=partially_filled`).
   A mutation that resolves within 1–2 reconciliation cycles is normal for partial fills.
2. If persistent (> 5 minutes): inspect the position record in Data Manager and compare
   with the Binance positionRisk `positionAmt`.  Identify which side is wrong and update
   accordingly.

### `raw_journal_count_mismatch` — internal count disagrees with exchange truth

**#592 root cause (fixed):** `PositionReconciler` was constructed in `api.py` without its
optional `store=` kwarg, so the AC1 (446-B) REST self-heal at the end of every
`reconcile_once()` pass — which overwrites the `ExchangeTruthStore` with that cycle's fresh
`positionRisk` snapshot — was dead code. The store (which backs
`position_manager.get_positions()` when `TE_EXCHANGE_TRUTH_STORE_ENABLED=on`) then only
self-corrected on a WebSocket reconnect/reseed or a live `ACCOUNT_UPDATE` event; a single
missed/late close event left it stale indefinitely. `store=` is now wired from
`dispatcher.user_data_consumer.store` at startup, so the store — and therefore the accessor
count — re-syncs to Binance REST truth every reconciliation cycle regardless of WS gaps.

Triage if it still fires after the #592 fix:

1. Compare `/state`'s `portfolio.open_positions_count` against `/positions`' record count
   (`.pagination.total`, or `len(.data)`) for the same account — they should now always agree
   (both are sourced from `get_positions()` post-#587).
2. If they still disagree, or the metric fires with `TE_EXCHANGE_TRUTH_STORE_ENABLED=off`,
   another code path is likely reading `position_manager.positions` directly instead of via
   `get_positions()` — grep for `.positions[` / `.positions.items()` / `.positions.values()`
   usages outside `position_manager.py` itself.
3. A lingering raw-journal-only entry (never touched by a real fill event, e.g. a
   pre-#592 ghost that predates the remediator) is voided the next time it also surfaces
   as a `ghost` divergence — force it with `POST /admin/reconcile-positions`.
4. This category never mutates state itself; it is purely diagnostic. It degrades the
   verdict (see "Verdict states") rather than blocking intake.

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
- [#587](https://github.com/PetroSa2/petrosa-tradeengine/issues/587) — introduced `raw_journal_count_mismatch`
- [#592](https://github.com/PetroSa2/petrosa-tradeengine/issues/592) — `store=` wiring fix (root cause) + `GhostPositionRemediator` (auto-void) + ternary verdict + `POST /admin/reconcile-positions`
- FR65, FR21, FR66 — PRD contract references

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `POSITION_RECONCILIATION_ENABLED` | `true` | Set `false` to disable (e.g. pure simulation runs) |
| `POSITION_RECONCILIATION_INTERVAL_SECONDS` | `60` | Cadence in seconds |
| `GHOST_POSITION_REMEDIATION_MODE` | `void` | `void` (default, auto-close stale journal entries) / `dry_run` (log only) / `off` (per #592) |

The reconciler is automatically **disabled** when `SIMULATION_ENABLED=true` (no real
Binance positions to reconcile against in simulation mode).
