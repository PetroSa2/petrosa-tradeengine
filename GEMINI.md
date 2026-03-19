# Engineering Mandate: Heartbeat Monitoring & RESTRICTED_MODE

## 🚀 Context
The Petrosa Trading Engine includes a critical safety fail-safe called `RESTRICTED_MODE`. This mode prevents the engine from executing "OPEN" signals on Binance when it cannot confirm that the governance service (CIO) is alive and healthy.

## 🛠 Standardized Heartbeat Subject
As of Ticket #287, the ecosystem-wide heartbeat subject is standardized to:
**`cio.heartbeat`**

### Previous Incorrect Subjects
- `cio.nurse.heartbeat` (RETIRED)

## 🚨 RESTRICTED_MODE Logic
The `HeartbeatMonitor` in `tradeengine/services/heartbeat_monitor.py` is responsible for tracking heartbeats on the standardized subject.

1.  **Entry Condition**: If no valid `HeartbeatMessage` is received within the configured `timeout` (default: 30s), the engine enters `RESTRICTED_MODE`.
2.  **Impact**: While in `RESTRICTED_MODE`, all `signal_id` processing for opening new positions is aborted.
3.  **Exit Condition**: To return to `NORMAL_MODE`, the engine must receive a continuous stream of valid heartbeats exceeding the `recovery_threshold` (default: 3 heartbeats).

## 📄 Implementation Requirements
- **Configuration**: Always use `NATS_TOPIC_HEARTBEAT` environment variable for the subject.
- **Fail-Safe Parity**: Any changes to the heartbeat monitoring logic must maintain parity between the `cio` publisher and `tradeengine` monitor.
- **Observability**: Transitions into and out of `RESTRICTED_MODE` must be logged as `CRITICAL` and `INFO` events respectively.
