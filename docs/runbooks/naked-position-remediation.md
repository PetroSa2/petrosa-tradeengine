# Runbook: Naked-Position Remediation Mode

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

## Related

- [#445](https://github.com/PetroSa2/petrosa-tradeengine/issues/445) — remediator implementation
- [#500](https://github.com/PetroSa2/petrosa-tradeengine/issues/500) — this alert / config fix
- [#501](https://github.com/PetroSa2/petrosa-tradeengine/issues/501) / [#502](https://github.com/PetroSa2/petrosa-tradeengine/issues/502) / [#503](https://github.com/PetroSa2/petrosa-tradeengine/issues/503) — SL/TP price fixes (gate for `arm_*`)
- [unhedged-positions.md](./unhedged-positions.md) — detection-side runbook
