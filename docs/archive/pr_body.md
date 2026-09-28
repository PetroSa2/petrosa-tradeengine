## Changes
- Standardized `NATS_TOPIC_HEARTBEAT` to `cio.heartbeat`.
- Updated `Settings` to read `NATS_TOPIC_HEARTBEAT` from environment.
- Updated `Dispatcher` to pass heartbeat subject to `HeartbeatMonitor`.
- Fixed `ImportError` for `datetime.UTC` in `shared/mysql_client.py` for Python 3.10 compatibility.
- Added `GEMINI.md` for heartbeat monitoring documentation.

Part of #287.
