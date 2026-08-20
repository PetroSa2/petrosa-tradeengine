# Position/Order State Truth — Mirror vs Always-Consult Decision

**Last Updated**: August 20, 2026
**Service Status**: ✅ ACTIVE (Production)
**Issue**: [#549](https://github.com/PetroSa2/petrosa-tradeengine/issues/549) — INVESTIGATION (P1)
**Root cause of**: `-4130` / `-4509` arm failures; malformed `source:"exchange"` quantities

---

## Summary

The trade engine keeps **three independent in-memory stores** of position/order state that
can silently diverge from Binance truth.
The SL/TP arming hot path reads local/result state, never live exchange qty.
That drift is the root cause of the live `-4509` (arming a position Binance no longer holds)
and `-4130` (arming legs that already exist) failures observed on 2026-08-20.

**Decision: Option A — corrected mirror.**
Keep `ExchangeTruthStore` as the single read model; close the four wiring gaps that make it
untrustworthy today.
Option B (always consult Binance) is rejected as the primary path because it adds a live REST
dependency to the per-order hot arming path — which today makes **zero** live calls — with **no
existing TTL cache or rate-limit gate** to build on.

---

## Evidence (live, futures testnet — pod `v1.2.17-r176-50367b3`)

- Arming loop detected 11 unhedged positions, computed correct clamped SLs, yet placement failed:
  - `APIError(-4509): Time in Force (TIF) GTE can only be used with open positions.`
  - `APIError(-4130): An open stop or take profit order with GTE and closePosition ... existing.`
- `GET /positions` returned `LTCUSDT LONG qty=-0.303` stamped `source:"exchange"` — a malformed
  signed value that never came from a live REST call.

---

## Root cause (code-level)

### Three local stores, none guaranteed fresh vs REST

| Store | Location | Feed | Drift mechanism |
|-------|----------|------|-----------------|
| `PositionManager.positions` dict | `position_manager.py:56` | `update_position()` mutates memory first | DM sync has a **swallowed 2s timeout** (`position_manager.py:523-536`); memory advances even when the DB write is dropped |
| `ExchangeTruthStore` snapshot | `exchange_truth_store.py:94-109` | user-data WS + REST seed on connect only | keeps **raw signed** `positionAmt` (`:142,204,245`) — a bad WS event surfaces `-0.303` for a LONG |
| `OCOManager.active_oco_pairs` | `dispatcher.py:106-108` | in-memory dedup dict | **resets on restart**; rebuilt from exchange at boot (`dispatcher.py:1976`) but empty in the crash→boot window |

### The four specific gaps that make the mirror lie

1. **REST write-back is wired but dead.** `PositionReconciler` is constructed at `api.py:266`
   **without** `store=`; constructor default is `store=None` (`position_reconciler.py:250`), so the
   60s `update_from_rest` authority-refresh (`position_reconciler.py:335-353`) never runs in
   production. (The strategy reconciler at `dispatcher.py:2005-2007` *does* pass the store — proof
   the plumbing works when wired.)
2. **No sign normalization.** `quantity=qty` stores the raw signed `positionAmt` in every path
   (WS `:142,151`; seed `:204,209`; reconcile `:245,251`). A LONG can hold a negative quantity.
3. **`source` label is a global config flag, not provenance.** `api.py:1502-1508` sets
   `"source": "exchange" if TE_EXCHANGE_TRUTH_STORE_ENABLED == "on" else "local"`. No per-record
   origin, timestamp, or staleness.
4. **Live arming gate is off by default.** The only entry-arming code that consults live
   positionRisk is the AC3 gate (`dispatcher.py:189-218`), gated by `TE_OCO_AC3_GATE_ENABLED=0`
   (default off). The primary arming path takes qty from the order result
   (`dispatcher.py:4357` `filled_quantity = result.get("amount")`), never from the exchange.

### Arming path summary

- **Entry-time arming** `_place_risk_management_orders()` (`dispatcher.py:4052+`): qty from
  `result.amount` / `order.amount`, side from local `order.position_side`. **No live read.** →
  `-4130` on stale/oversized qty, `-4509` when the position no longer exists.
- **AC3 gate** (`dispatcher.py:189-218`): live positionRisk **presence** check, off by default;
  gates presence only, does not override the qty.
- **Remediator** `_rearm()` (`naked_position_remediator.py:236-364`): exchange-authoritative
  (`div["binance_qty"]`, `div["side"]`), but off by default and runs only on the 60s reconciler
  cadence.

---

## Options considered

### Option A — corrected mirror (CHOSEN)

Keep `ExchangeTruthStore` as the single read model; make it trustworthy:

- (a) Wire FR65 reconciler with `store=` at `api.py:266` so REST periodically corrects the store.
- (b) Normalize hedge/one-way sign at the store boundary so a LONG never stores negative qty.
- (c) Turn on the live arming gate so arming keys qty/side off exchange truth (fixes -4130/-4509).
- (d) Make `source` per-record provenance + `stale_seconds`, replacing the global flag.
- (e) Collapse the three read paths onto one read model over time.

**Pros**: hot arming path stays fast (no per-order live call); reuses existing WS feed; gaps are
small, localized wiring fixes; keeps the 60s REST authority-refresh already designed (FR65).
**Cons**: still a cache — correctness depends on the write-back + gate actually being on; requires
provenance discipline to avoid re-introducing stale reads.

### Option B — always consult Binance (REJECTED as primary)

Remove the position mirror from decision paths; arming/coverage/`GET /positions` call
`positionRisk` / `openAlgoOrders` live with a short TTL cache.

**Pros**: no drift by construction; `source` is trivially truthful.
**Cons**: **no existing TTL cache or rate-limit gate** — `RateLimitMonitor`
(`services/rate_monitor.py`) is observability-only (parses `x-mbx-used-weight-1m`, never throttles).
Arming is currently a hot per-order path with **zero** live calls (`dispatcher.py:4357`); Option B
adds a live REST dependency + latency there. `get_position_info()` (`binance.py:1850`) is a
synchronous client call that blocks the loop. Net-new cache + weight-aware gate would be required.

---

## Rate-limit budget (Option B feasibility sizing — AC1)

Current steady-state REST cost from the reconciler loop (`shared/config.py:97`, default **60s**):

- 1× `positionRisk` (`get_position_info`, `binance.py:1850`) per pass.
- 1× `openAlgoOrders` (`get_open_algo_orders`, `binance.py:1865`) **per unique symbol holding a
  position** (`position_reconciler.py:388`).

So ≈ `1 + N_symbols` weighted REST calls / 60s.
`futures_position_information` weight is 5 (all symbols); `openAlgoOrders` ~1 each.
For a 13-position fleet that is ≈ `5 + 13 ≈ 18` weight / 60s — trivial against Binance's
2400 weight/min futures budget.

**Feasibility verdict**: Option B is *technically* affordable at reconciler cadence, but the cost
is not in the 60s loop — it is the **hot arming path** (currently zero live calls) and the 2s OCO
monitor (`dispatcher.py:116-117`). Adding synchronous live reads there is the real risk, and there
is no cache/throttle to absorb it. This reinforces Option A.

---

## Acceptance-criteria mapping

| AC | Resolution under Option A |
|----|---------------------------|
| Rate-limit budget quantified vs call frequency | See "Rate-limit budget" above (≈1+N / 60s; ≈18 weight/min for 13 positions vs 2400 budget). |
| Decision recorded (A or B) with rationale in `docs/` | **This document** — Option A. |
| `GET /positions` never disagrees with live `positionRisk`; `source` = true provenance + staleness | Gap (a) write-back + (d) per-record provenance/`stale_seconds` → follow-up ticket. |
| SL/TP arming keys qty/side off exchange truth (-4509/-4130 cannot fire normally) | Gap (c) enable live arming gate; arming reads exchange qty/side → follow-up ticket. |
| Regression: simulated stale/wrong local snapshot does not cause wrong-side/phantom arm | New regression test seeding a stale `positions` dict / bad WS event; assert no arm or correct-side arm. |
| FR65 reconciler write-back gap at `api.py:266` closed or obsoleted | Gap (a) — pass `store=` → follow-up ticket. |

---

## Follow-up implementation tickets

1. **FR65 write-back + sign-normalization** — pass `store=` at `api.py:266`; normalize
   `positionAmt` sign at the `ExchangeTruthStore` boundary (`exchange_truth_store.py:142,204,245`).
   (gaps a + b)
2. **Live arming gate default-on + qty source** — make arming read exchange qty/side; promote
   `TE_OCO_AC3_GATE_ENABLED` handling and route `filled_quantity` through live truth. (gap c) —
   overlaps with #547 (inverted-sign naked) and #550 (OCO divergence).
3. **Per-record provenance + staleness on `GET /positions`** — replace the global `source` flag
   (`api.py:1502-1508`) with per-record `source` + `stale_seconds`. (gap d)
4. **Single read model** — collapse `positions` dict / `ExchangeTruthStore` / `active_oco_pairs`
   consumers onto one read path. (gap e)

Tickets #547 and #550 are unblocked by this decision: both must key arming off exchange truth
(gap c), consistent with Option A.
