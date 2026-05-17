# Audit Logging Contract

This document describes the audit logging behavior of the Petrosa Trade Engine
and the `/health` contract that operators can rely on.

## Current state: stdout stub

The in-process audit logger at `shared/audit.py` is a **stdout stub**. Audit
events are emitted as structured log lines via Python's `logging` module. There
is **no remote persistence layer wired up** through this code path.

The previous implementation hardcoded `connected = False` while leaving the
underlying log calls operational. Dispatcher call sites gated audit writes on
`enabled and connected`, so every audit write silently no-op'd while `/health`
suggested a transient connectivity problem. Issue #354 aligns the names and
gates with reality.

## Health contract

`GET /health` returns an `audit_logger` component with the following shape:

```json
{
  "status": "healthy",
  "enabled": true,
  "backend": "stdout",
  "mode": "stub",
  "is_persistent": false
}
```

Field meanings:

- `status` — `"healthy"` when the logger is enabled and able to emit;
  `"disabled"` when `enabled` is False. Use this to decide whether the audit
  pipeline is functioning, not as a proxy for persistence.
- `enabled` — master switch. When False, every `log_*` call is a no-op and
  nothing is emitted anywhere.
- `backend` — destination identifier. Currently always `"stdout"`. A future
  persistent backend will report e.g. `"mongodb"` or `"mysql"`.
- `mode` — `"stub"` until a persistent backend is wired up. Switches to
  `"persistent"` when audit records are durably stored.
- `is_persistent` — boolean shorthand for "does this backend survive a pod
  restart?". False for the stub.

The legacy `connected` attribute is preserved on the `AuditLogger` instance for
backwards compatibility with tests that patch it, but it is no longer consulted
by any dispatcher or API gate. Treat it as deprecated; do not introduce new
references.

## Dispatcher behavior

Every audit write in `tradeengine/dispatcher.py` is gated on
`audit_logger.enabled` only. Disabling the logger (`enabled = False`) is the
sole way to suppress audit writes — for example, integration tests patch this
flag to silence audit noise.

## Related modules — do not conflate

A separate **MySQL-backed** `AuditLogger` lives in `shared/logger.py`. That
class is unrelated to the in-process stub described here. It exists for
NATS-driven persistent audit ingestion and has its own lifecycle. When wiring
up a persistent backend for the in-process logger, decide explicitly whether to
delegate to that module or introduce a new backend rather than collapsing the
two.

## Migrating to a persistent backend

When persistence is wired up, the following changes are expected:

1. `shared/audit.py` — set `backend` / `mode` / `is_persistent` class
   attributes to reflect the real backend (e.g. `backend = "mongodb"`,
   `mode = "persistent"`, `is_persistent = True`).
2. `health()` — extend the payload with backend-specific connectivity info
   (e.g. last-write timestamp, connection state). Operators reading
   `is_persistent: true` then need a separate signal that the backend is
   actually reachable; consider re-introducing a meaningful `connected`
   field at that point.
3. Dispatcher — gates remain `if audit_logger.enabled:`; the logger itself is
   responsible for graceful degradation if the persistent backend is
   unreachable.
4. Tests — the `mock_audit_logger` fixtures only need to patch `enabled`;
   keeping the legacy `connected` patch is harmless but no longer required.
