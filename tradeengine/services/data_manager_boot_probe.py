"""
Boot-time write-then-read self-test for DataManagerClient stub-regression guard.

Closes PetroSa2/petrosa-tradeengine#451 (AC1-AC6).
Catches the class of silent stub regression that went undetected for 7 months
(2025-10-23 to 2026-06-04). The probe runs once per pod boot, gates /readyz,
and emits structured logs + Prometheus counters so dashboards can track deployment
health over time.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

from tradeengine.services.data_manager_client import BaseDataManagerClient

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 20.0
_PROBE_COLLECTION = "tradeengine_boot_probes"
_PROBE_DB = "mongodb"
_TTL_HOURS = 24


@dataclass
class BootProbeResult:
    success: bool
    failure_mode: str | None = None
    probe_id: str | None = None
    detail: dict = field(default_factory=dict)


class DataManagerBootProbe:
    """
    Write-then-read self-test run once at pod startup.

    Writes a uniquely-tagged sentinel record to ``tradeengine_boot_probes``,
    reads it back, and confirms both inserted_id != "placeholder" and the
    round-trip returned the same record. Any failure flips /readyz to 503.

    Feature flag: TE_BOOT_PROBE_ENABLED (default: true). Set to "false" to
    disable during rollout if the probe itself misbehaves.
    """

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self._base_url = base_url
        self._timeout = timeout

    async def run(self, pod_name: str = "unknown") -> BootProbeResult:
        """Execute the boot probe within a hard 20-second total timeout (#465 AC1).

        Raised from 5s to 20s because ``dispatcher.initialize()`` can leave the
        event loop saturated by background tasks (sync Binance REST calls during
        leverage setup and OCO reconciliation) for several seconds after it
        returns. Data-manager write+read round-trip is ~300 ms, so 20 s gives
        ~19.7 s of event-loop slack — enough that any realistic contention
        clears before the probe coroutine reaches its first ``await``.
        """
        if not _probe_enabled():
            logger.info("dm_boot_probe.skipped TE_BOOT_PROBE_ENABLED=false")
            return BootProbeResult(success=True)

        utc_now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        probe_id = f"boot-probe-{pod_name}-{utc_now}"

        try:
            result = await asyncio.wait_for(
                self._run_probe(probe_id),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            failure_mode = f"probe_timeout_after_{_PROBE_TIMEOUT_SECONDS}s"
            logger.error(
                "dm_boot_probe.failure probe_id=%s failure_mode=%s",
                probe_id,
                failure_mode,
            )
            _increment_counter("failure")
            result = BootProbeResult(
                success=False, failure_mode=failure_mode, probe_id=probe_id
            )

        return result

    async def _run_probe(self, probe_id: str) -> BootProbeResult:
        created_at = datetime.now(UTC).isoformat()
        client = BaseDataManagerClient(base_url=self._base_url, timeout=self._timeout)
        try:
            return await self._probe_with_client(client, probe_id, created_at)
        finally:
            await client.close()

    async def _probe_with_client(
        self,
        client: BaseDataManagerClient,
        probe_id: str,
        created_at: str,
    ) -> BootProbeResult:
        # AC1: Write sentinel record. `probe_id` is duplicated outside `_id` because
        # data-manager's MongoDB adapter strips `_id` from query_range results
        # (mongodb_adapter.query_range line ~174), so the read-back filter must
        # use a field that survives that strip. Fixes petrosa-tradeengine#468.
        record = {"_id": probe_id, "probe_id": probe_id, "created_at": created_at}
        try:
            write_result = await client.insert_one(_PROBE_DB, _PROBE_COLLECTION, record)
        except Exception as exc:
            failure_mode = f"write_exception:{type(exc).__name__}"
            logger.error(
                "dm_boot_probe.failure probe_id=%s failure_mode=%s error=%s",
                probe_id,
                failure_mode,
                exc,
            )
            _increment_counter("failure")
            return BootProbeResult(
                success=False, failure_mode=failure_mode, probe_id=probe_id
            )

        inserted_id = write_result.get("inserted_id", "")
        # Detect the old stub which always returned {"inserted_id": "placeholder"}
        if not inserted_id or inserted_id == "placeholder":
            failure_mode = f"placeholder_id:{inserted_id!r}"
            logger.error(
                "dm_boot_probe.failure probe_id=%s failure_mode=%s",
                probe_id,
                failure_mode,
            )
            _increment_counter("failure")
            return BootProbeResult(
                success=False, failure_mode=failure_mode, probe_id=probe_id
            )

        # AC1: Read back the sentinel record
        try:
            read_result = await client.query(
                _PROBE_DB, _PROBE_COLLECTION, filter={"probe_id": probe_id}, limit=1
            )
        except Exception as exc:
            failure_mode = f"readback_exception:{type(exc).__name__}"
            logger.error(
                "dm_boot_probe.failure probe_id=%s failure_mode=%s error=%s",
                probe_id,
                failure_mode,
                exc,
            )
            _increment_counter("failure")
            return BootProbeResult(
                success=False, failure_mode=failure_mode, probe_id=probe_id
            )

        data = read_result.get("data", [])
        if not data:
            failure_mode = "readback_empty"
            logger.error(
                "dm_boot_probe.failure probe_id=%s failure_mode=%s",
                probe_id,
                failure_mode,
            )
            _increment_counter("failure")
            return BootProbeResult(
                success=False, failure_mode=failure_mode, probe_id=probe_id
            )

        # AC4: Best-effort cleanup of probe records older than TTL hours
        _cleanup_old_probes_sync(client, probe_id)

        logger.info(
            "dm_boot_probe.success probe_id=%s inserted_id=%s",
            probe_id,
            inserted_id,
        )
        _increment_counter("success")
        return BootProbeResult(success=True, probe_id=probe_id)


def _cleanup_old_probes_sync(
    client: BaseDataManagerClient, current_probe_id: str
) -> None:
    """Fire-and-forget cleanup of probe records older than _TTL_HOURS (AC4)."""
    import asyncio

    async def _do_cleanup() -> None:
        cutoff = (datetime.now(UTC) - timedelta(hours=_TTL_HOURS)).isoformat()
        try:
            await client.delete(
                _PROBE_DB,
                _PROBE_COLLECTION,
                filter={
                    "created_at": {"$lt": cutoff},
                    "_id": {"$ne": current_probe_id},
                },
            )
        except Exception:
            pass  # Cleanup failures never block the probe

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do_cleanup())
    except Exception:
        pass


def _probe_enabled() -> bool:
    return os.getenv("TE_BOOT_PROBE_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def _increment_counter(result: str) -> None:
    """Increment Prometheus + OTel boot probe counter (AC6). Never raises."""
    try:
        from tradeengine.metrics import dm_boot_probe_total, otel_dm_boot_probe_total

        dm_boot_probe_total.labels(result=result).inc()
        otel_dm_boot_probe_total.add(1, {"result": result})
    except Exception:
        pass
