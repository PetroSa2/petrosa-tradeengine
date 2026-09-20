# Runbook: Naked-Position Remediation Mode

**GitHub tooling:** Use the official `github` MCP server for linked issue, pull
request, review, release, or Actions operations, and `github-projects` MCP for
Projects v2. Use `gh` only for non-MCP clients, deterministic scripts, runners, or
unsupported operations, with authentication from the configured file-backed token
or environment.

**Alert:** `tradeengine-naked-remediation-off` (Grafana Cloud)
**Metric:** `tradeengine_naked_position_remediation_mode_status{mode="off"} > 0`
**Severity:** `critical`
**Origin:** [#500](https://github.com/PetroSa2/petrosa-tradeengine/issues/500) (2026-07-16 naked-position incident)

---

## What this alert means

The exchange-authoritative `NakedPositionRemediator` (#445) is running in **`off`**
mode. In `off` mode the watchdog **detects** naked (unprotected) positions and
increments `tradeengine_naked_position_detected_total`, but takes **no corrective
write action** — it never re-arms protective stops and never flattens. This is a
"watchdog that never enforces."

On 2026-07-16 the remediator silently ran `off` in production because
`TE_NAKED_POSITION_REMEDIATION_MODE` was unset and the code default was `off`.
Multiple live positions (XRPUSDT, BTCUSDT, ETHUSDT) sat naked with zero
protective orders while metrics incremented and no repair happened.

Since #500:
- The code default is **`dry_run`** (`shared/config.py`), so a fresh deploy is
  never silently `off`.
- `k8s/tradeengine/deployment.yaml` sets `TE_NAKED_POSITION_REMEDIATION_MODE`
  **explicitly**.
- The effective mode is logged at startup and exported as
  `tradeengine_naked_position_remediation_mode_status{mode="..."}`.

If this alert fires, the env was mis-set (or coerced to `off` from a garbage
value) and enforcement is disabled.

---

## Remediation modes

| Mode             | Detects | Re-arms SL/TP | Flattens | Exchange writes |
|------------------|:-------:|:-------------:|:--------:|:---------------:|
| `off`            | ✅      | ❌            | ❌       | none            |
| `dry_run`        | ✅      | ❌ (logs)     | ❌ (logs)| none            |
| `arm_only`       | ✅      | ✅            | ❌       | re-arm only     |
| `arm_or_flatten` | ✅      | ✅            | ✅ (after grace) | re-arm + flatten |

---

## Enablement order (AC4)

Promote in this exact order, validating each step on a canary before advancing:

```
dry_run  →  arm_only  →  arm_or_flatten
```

1. **`dry_run`** — validate the remediator correctly identifies real naked
   positions and that its *intended* actions (logged, not executed) look
   correct against live divergences.
2. **`arm_only`** — allow it to re-arm protective SL/TP but never flatten.
   Confirm re-armed orders land at correct prices on Binance.
3. **`arm_or_flatten`** — full enforcement: re-arm, and flatten as a fallback
   after the grace window (`TE_NAKED_POSITION_FLATTEN_GRACE_SEC`, default 60s).

> **Gate:** `arm_*` modes reuse the SL/TP reference-price logic. That logic was
> the subject of the price-computation cluster #501 / #502 / #503, all of which
> are now **closed/merged** — so promotion to `arm_or_flatten` is unblocked.
> Re-verify the price fixes are deployed before promoting to any `arm_*` mode.

---

## How to change the mode

Edit `k8s/tradeengine/deployment.yaml` in `petrosa_k8s`:

```yaml
- name: TE_NAKED_POSITION_REMEDIATION_MODE
  value: "dry_run"   # or arm_only / arm_or_flatten
```

Commit via GitOps and let the deploy roll. Confirm the new mode at startup:

```bash
kubectl -n petrosa-apps logs deploy/petrosa-tradeengine | grep naked_remediation_mode
```

and via the metric:

```
tradeengine_naked_position_remediation_mode_status{mode="arm_or_flatten"} == 1
```

---

## Verification

- Startup log shows `naked_remediation_mode=<mode>` and, if `off`, a loud
  warning.
- `tradeengine_naked_position_remediation_mode_status{mode="off"} == 0` (alert
  clears).
- Exactly one mode series equals `1`.

---

## Malformed (sign-inverted) positions — #547 / #586 / #607

A **malformed** position is a hedge-mode row where the declared
`positionSide` disagrees with the sign of `positionAmt` (e.g.
`positionSide=LONG` with a *negative* `positionAmt`). It is un-armable: any
`reduceOnly` SL/TP derived from the declared side would be direction-invalid.

> **⚠️ `arm_only` does NOT remediate malformed positions.** By design it can
> only re-arm ordinary `unhedged` divergences; a malformed position is
> un-armable (see above) so `arm_only` can only alert and wait — it never
> flattens. If you rely on `arm_only` in production, a malformed position
> **will** sit naked (missing SL and/or TP) until an operator intervenes or
> the mode is promoted to `arm_or_flatten`. This was the exact dead path
> behind the 2026-09-20 BCHUSDT/XRPUSDT incident (#607): both positions sat
> without TP for 1h15m while `arm_only` logged one CRITICAL alert and then
> went silent. **`arm_or_flatten` is the recommended production mode** if
> malformed positions are a realistic occurrence on your account (hedge-mode
> sign inversions have recurred across #547/#566/#586/#607).

- In `arm_only` mode, `_handle_malformed` alerts immediately on first
  detection (`tradeengine_malformed_position_total` +
  `tradeengine_malformed_position_stuck_seconds`, plus a CRITICAL log) and
  **re-alerts every `TE_NAKED_POSITION_MALFORMED_REALERT_INTERVAL_SEC`**
  (default 300s / `shared/config.py::naked_position_malformed_realert_interval_sec`)
  while the position remains stuck (#607 — previously this fired exactly
  once per episode and then went silent for the position's entire remaining
  lifetime). Each alert explicitly recommends promoting to `arm_or_flatten`.
  It **never** attempts to flatten — the position stays stuck by design
  until an operator acts or the mode is promoted.
- In `arm_or_flatten` mode, the same handler flattens it via
  `close_position_with_cleanup` after `flatten_grace_sec`.
- `tradeengine_remediation_mode_divergence_counts{mode,category}` (#607)
  exports a per-mode, per-category breakdown of every divergence the
  remediator sees each cycle (e.g. `{mode="arm_only",
  category="malformed_position"}`), so an operator can read "arm_only
  detected 3 unhedged + 2 malformed this cycle" directly off one metric
  instead of cross-referencing separate per-category counters.
- Recommended alert rule (add to `petrosa_k8s/observability/alert-rules/`):
  `tradeengine_malformed_position_stuck_seconds > 300` for `5m` at
  `critical` — the stuck-seconds gauge already tracks live age continuously
  independent of the alert-repeat interval above.

### Root cause (#586, 2026-09-16 LTCUSDT incident)

`close_position_with_cleanup` (and the remediator's `_flatten`, which calls
it) send a MARKET close order with a caller-supplied `quantity`. In hedge
mode, `BinanceFuturesExchange._execute_market_order` **omits** the wire
`reduceOnly` field whenever `positionSide` is set — Binance rejects the
`reduceOnly` + `positionSide` combination outright, so `positionSide` alone
is relied on to fix *direction*. Critically, this means **the exchange
applies no server-side cap on the closing quantity**: a stale or duplicate
`quantity` larger than the live position (e.g. two independent close
triggers racing on the same position, or a delayed close firing after the
position was already partially reduced elsewhere) fully executes and
overshoots past zero, flipping the position's sign — exactly the malformed
terminal state this section describes.

**Fix (#586):** `close_position_with_cleanup` now clamps the requested
`quantity` to a confidently-read live Binance position size immediately
before emission (`ExchangeTruthStore` when ready, else a REST re-read),
refusing the close entirely if the live position is confidently already
flat, and passing the pre-clamp `quantity` through unclamped only when the
live reading is genuinely unknown (never treating an ambiguous "0" as
confidently flat — mirroring the existing #481 AC3 "unknown must not block"
rule). This protects every caller of `close_position_with_cleanup`,
including the remediator's `_flatten`, transitively. See
`tradeengine/dispatcher.py::close_position_with_cleanup` step 1d and
`tests/test_dispatcher_close_guard.py` (`test_586_*`) for the guard and its
regression coverage.

The exact concurrent-trigger sequence that produced the live LTCUSDT
incident was not captured with enough granularity to name definitively —
the two most structurally plausible candidates in this codebase are the
OCO-placement-failure atomic rollback (`dispatcher.py` ~line 3148) and the
`#551` market-crossed-stop flatten (`dispatcher.py` ~line 4600) firing for
the same position — both compute their closing quantity from the same
just-filled entry order and neither previously validated against the live
exchange position before emission. The #586 clamp closes that gap
regardless of which path (or a future one) triggers it.

### Operator remediation for a currently-stuck malformed position

`scripts/remediate-malformed-position.py` — scan (default, read-only) or
flatten (`--apply`) any live malformed position:

```bash
# Read-only scan — always safe, lists every malformed row as JSON
python scripts/remediate-malformed-position.py

# Flatten all malformed positions found (double opt-in required)
python scripts/remediate-malformed-position.py --apply --yes-i-am-sure

# Scope to one symbol
python scripts/remediate-malformed-position.py --apply --yes-i-am-sure \
    --symbol LTCUSDT
```

The script re-reads the live position immediately before closing (never the
earlier scan's quantity) and refuses to run `--apply` without both
`BINANCE_API_KEY`/`BINANCE_API_SECRET` **and** a second explicit opt-in
(`--yes-i-am-sure` or `TE_REMEDIATE_MALFORMED_ACK=1`).

Equivalent manual alternatives referenced in #586: `scripts/close-test-position.py`
(BTCUSDT-only, one-way-mode oriented) or `scripts/close_all_binance_positions.py`.

> **This automated BMAD ticket-orchestrator run did not execute this script.**
> Per the hard safety constraint against autonomous live/testnet exchange
> writes, flattening the currently-stuck LTCUSDT position (AC1 of #586) is
> left as an explicit operator action using the command above — the code fix
> above (AC3–AC5) prevents recurrence going forward regardless of when the
> existing stuck position is cleared.

---

## Related

- [#445](https://github.com/PetroSa2/petrosa-tradeengine/issues/445) — remediator implementation
- [#500](https://github.com/PetroSa2/petrosa-tradeengine/issues/500) — this alert / config fix
- [#501](https://github.com/PetroSa2/petrosa-tradeengine/issues/501) / [#502](https://github.com/PetroSa2/petrosa-tradeengine/issues/502) / [#503](https://github.com/PetroSa2/petrosa-tradeengine/issues/503) — SL/TP price fixes (gate for `arm_*`)
- [#547](https://github.com/PetroSa2/petrosa-tradeengine/issues/547) — malformed-position terminal-state handling (`_handle_malformed`)
- [#586](https://github.com/PetroSa2/petrosa-tradeengine/issues/586) — this section: sign-inversion root cause + live-qty clamp fix
- [unhedged-positions.md](./unhedged-positions.md) — detection-side runbook
