# Halt Trading Now

The tradeengine reads `TRADING_HALT` from its deployment environment.
When it is true, new-entry orders are rejected with `manual_halt`.
Reduce-only close, scale-out, take-profit, and stop-loss orders continue to run.

## QA

Set the halt and let Kubernetes roll the QA deployment:

```bash
kubectl -n petrosa-apps set env deployment/qa-tradeengine TRADING_HALT=true
```

Verify `GET /health` reports `components.manual_halt.active: true` and the
Prometheus metric `petrosa_tradeengine_manual_halt` is `1`.

Re-enable entries with the inverse command:

```bash
kubectl -n petrosa-apps set env deployment/qa-tradeengine TRADING_HALT=false
```

Verify the health state is false and the gauge is `0` before running the QA
entry, close, and stop-loss checks.

These commands are restricted to `qa-tradeengine`; do not run them against a
production deployment or production account.
