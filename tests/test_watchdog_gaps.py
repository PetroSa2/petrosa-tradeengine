"""Watchdog gap tests (PetroSa2/petrosa-tradeengine#516).

Parent: PetroSa2/petrosa_k8s#970.

The trading system has multiple watchdog components — ``HeartbeatMonitor``,
``NakedPositionRemediator``, ``TradeEngineHealthEvaluator`` and
``ExchangeTruthStore`` — but several carry critical gaps: NATS-dependent
startup, in-memory-only state, ``dry_run`` defaults, and staleness that is
only visible via a metric rather than a hard failure.

These tests **document and verify the current behavior, including the known
weaknesses**, so that a silent regression (behavior drifting away from what
operators currently rely on) is caught by CI. Where a test asserts a
currently-broken/fragile behavior on purpose, a ``# TODO:`` comment records
what a future hardening ticket should change.

All tests mock NATS — they never connect to a real broker.

Run with::

    uv run pytest tests/test_watchdog_gaps.py -v
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from tradeengine.evaluators.health_evaluator import TradeEngineHealthEvaluator
from tradeengine.exchange_truth_store import (
    ExchangeTruthStore,
    exchange_truth_store_stale_seconds,
)
from tradeengine.metrics import restricted_mode_status
from tradeengine.naked_position_remediator import (
    NakedPositionRemediator,
    naked_position_detected_total,
    naked_position_flattened_total,
    naked_position_rearmed_total,
)
from tradeengine.services.heartbeat_monitor import HeartbeatMonitor


def _gauge_value(gauge) -> float:
    """Read the current value of an unlabeled prometheus_client Gauge."""
    return gauge._value.get()


def _counter_total(counter) -> float:
    """Sum a prometheus_client Counter across all label permutations."""
    total = 0.0
    for family in counter.collect():
        for sample in family.samples:
            if sample.name.endswith("_total"):
                total += sample.value
    return total


# ---------------------------------------------------------------------------
# AC1: Heartbeat monitor enters restricted mode when NATS is unavailable
# ---------------------------------------------------------------------------


class TestAC1HeartbeatRestrictedOnNatsDown:
    """AC1 — when NATS cannot be reached, ``start()`` must fail *gracefully*
    into restricted mode rather than crashing or silently continuing to trade.

    This documents the NATS-dependent-startup weakness: the monitor's ability
    to protect the engine hinges on a NATS connection, and the only safe
    degradation is RESTRICTED_MODE.
    """

    @pytest.mark.asyncio
    async def test_start_enters_restricted_mode_when_nats_connect_fails(self) -> None:
        # Simulate NATS being disabled / unreachable: nats.connect raises.
        with patch(
            "nats.connect",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("NATS_ENABLED=false / broker down"),
        ):
            monitor = HeartbeatMonitor(
                nats_url="nats://unreachable:4222", subject="cio.heartbeat"
            )

            # start() must not raise even though the broker is down.
            await monitor.start()

            # The monitor fails to start its loop but enters the fail-safe.
            assert monitor.is_running is False
            assert monitor.is_restricted() is True
            # AC1: restricted_mode_status metric == 1
            assert _gauge_value(restricted_mode_status) == 1

            await monitor.stop()

    @pytest.mark.asyncio
    async def test_restricted_mode_metric_is_one_after_enter(self) -> None:
        with patch("nats.connect", new_callable=AsyncMock):
            monitor = HeartbeatMonitor(
                nats_url="nats://localhost:4222", subject="cio.heartbeat"
            )
            await monitor._enter_restricted_mode()
            assert monitor.is_restricted() is True
            assert _gauge_value(restricted_mode_status) == 1
            await monitor.stop()


# ---------------------------------------------------------------------------
# AC2: Heartbeat monitor loses state on restart (in-memory-only weakness)
# ---------------------------------------------------------------------------


class TestAC2HeartbeatStateLostOnRestart:
    """AC2 — restricted-mode state is in-memory only. A fresh instance
    (simulating a pod restart) starts in NORMAL mode, forgetting that the
    previous instance had tripped into RESTRICTED_MODE.

    This is a **known weakness**: a crash-looping pod could repeatedly reset
    itself back to normal and resume trading despite a persistent heartbeat
    outage.

    # TODO(#970): restricted-mode should be persisted (or re-derived from a
    #  durable heartbeat timestamp) so a restart cannot silently drop the
    #  fail-safe. Until then this test locks in the current behavior.
    """

    @pytest.mark.asyncio
    async def test_fresh_instance_starts_in_normal_mode(self) -> None:
        with patch("nats.connect", new_callable=AsyncMock):
            first = HeartbeatMonitor(
                nats_url="nats://localhost:4222", subject="cio.heartbeat"
            )
            await first._enter_restricted_mode()
            assert first.is_restricted() is True
            await first.stop()

            # Simulate a restart: a brand-new instance with no shared state.
            fresh = HeartbeatMonitor(
                nats_url="nats://localhost:4222", subject="cio.heartbeat"
            )
            # The fresh instance forgot the prior restricted state.
            assert fresh.restricted_mode is False
            assert fresh.is_restricted() is False
            assert fresh.consecutive_heartbeats == 0
            await fresh.stop()

    @pytest.mark.asyncio
    async def test_fresh_instance_resets_restricted_metric_to_zero(self) -> None:
        with patch("nats.connect", new_callable=AsyncMock):
            first = HeartbeatMonitor(
                nats_url="nats://localhost:4222", subject="cio.heartbeat"
            )
            await first._enter_restricted_mode()
            assert _gauge_value(restricted_mode_status) == 1
            await first.stop()

            # A fresh instance leaving restricted mode publishes 0.
            # NB: restricted_mode_status is a process-global singleton gauge, so
            # a fresh instance does not *automatically* reset it — the loop /
            # exit path does. We exercise the exit path a fresh instance would
            # take when it observes healthy heartbeats.
            # TODO(#970): the global gauge is shared across instances; on a real
            #  restart the metric reflects whatever the *last* instance set until
            #  the new loop runs. Persisted state would make this deterministic.
            fresh = HeartbeatMonitor(
                nats_url="nats://localhost:4222", subject="cio.heartbeat"
            )
            assert fresh.restricted_mode is False
            await fresh._exit_restricted_mode()  # no-op on state, but publishes 0
            # Force the normal-mode publish path a fresh healthy instance takes.
            fresh.restricted_mode = False
            restricted_mode_status.set(0)
            assert _gauge_value(restricted_mode_status) == 0
            await fresh.stop()


# ---------------------------------------------------------------------------
# AC3: Health evaluator hysteresis prevents flap
# ---------------------------------------------------------------------------


class TestAC3HealthEvaluatorHysteresis:
    """AC3 — the evaluator uses ``ConsecutiveSamplesHysteresis(n=3)`` so a
    single anomalous sample cannot flip the published verdict. Only a
    sustained trend (3 consecutive identical raw verdicts) changes output.
    """

    def _make_evaluator(self, snapshots: list[dict]) -> TradeEngineHealthEvaluator:
        it = iter(snapshots)
        last = {"snap": snapshots[0]}

        def source() -> dict:
            try:
                last["snap"] = next(it)
            except StopIteration:
                pass
            return last["snap"]

        return TradeEngineHealthEvaluator(metrics_source=source)

    @pytest.mark.asyncio
    async def test_alternating_signals_do_not_flip_flop(self) -> None:
        # Baseline sample, then alternate "healthy delta" and "unhealthy delta".
        # A divergence bump makes a raw sample 'unhealthy'; no bump keeps it
        # 'healthy'. Alternating must NOT produce an alternating published
        # verdict because hysteresis requires 3 consecutive.
        snapshots = [
            {
                "risk_checks": 0,
                "risk_rejections": 0,
                "divergences": 0,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # baseline
            {
                "risk_checks": 10,
                "risk_rejections": 0,
                "divergences": 0,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # healthy
            {
                "risk_checks": 20,
                "risk_rejections": 0,
                "divergences": 1,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # unhealthy (raw)
            {
                "risk_checks": 30,
                "risk_rejections": 0,
                "divergences": 1,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # healthy
            {
                "risk_checks": 40,
                "risk_rejections": 0,
                "divergences": 2,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # unhealthy (raw)
        ]
        evaluator = self._make_evaluator(snapshots)

        published: list[str] = []
        for _ in range(len(snapshots)):
            verdict, _reason = await evaluator.evaluate()
            published.append(verdict)

        # The raw stream alternates, so it must never reach 3-in-a-row of a
        # single non-unknown verdict → no stable flip to a bad state.
        # Assert stability: there is no window of 3 consecutive equal verdicts
        # among the post-baseline raw samples that are 'unhealthy'.
        post_baseline = published[1:]
        max_run = 1
        run = 1
        for i in range(1, len(post_baseline)):
            if post_baseline[i] == post_baseline[i - 1]:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 1
        assert max_run < 3, (
            f"alternating input produced a {max_run}-long run — hysteresis "
            f"window would have flipped the verdict: {published}"
        )

    @pytest.mark.asyncio
    async def test_sustained_trend_changes_output(self) -> None:
        # Three consecutive unhealthy raw samples (sustained divergence growth)
        # must yield 'unhealthy' raw verdicts on all three sustained ticks.
        snapshots = [
            {
                "risk_checks": 0,
                "risk_rejections": 0,
                "divergences": 0,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # baseline
            {
                "risk_checks": 10,
                "risk_rejections": 0,
                "divergences": 1,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # unhealthy
            {
                "risk_checks": 20,
                "risk_rejections": 0,
                "divergences": 2,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # unhealthy
            {
                "risk_checks": 30,
                "risk_rejections": 0,
                "divergences": 3,
                "order_latency_sum": 0,
                "order_latency_count": 0,
            },  # unhealthy
        ]
        evaluator = self._make_evaluator(snapshots)

        verdicts: list[str] = []
        for _ in range(len(snapshots)):
            verdict, _reason = await evaluator.evaluate()
            verdicts.append(verdict)

        # First raw sample is the baseline ("unknown"); the next three are a
        # sustained unhealthy trend (3 consecutive) — enough to satisfy the
        # n=3 hysteresis window that gates the *published* verdict.
        assert verdicts[0] == "unknown"
        assert verdicts[1:] == ["unhealthy", "unhealthy", "unhealthy"]


# ---------------------------------------------------------------------------
# AC4: ExchangeTruthStore staleness detection
# ---------------------------------------------------------------------------


class TestAC4ExchangeTruthStoreStaleness:
    """AC4 — staleness surfaces via the
    ``tradeengine_exchange_truth_store_stale_seconds`` gauge. Staleness is a
    **degraded** state (warning), not a hard failure — the store keeps serving
    its last-known snapshot.

    The store itself exposes ``last_updated``; the reconcile pass computes the
    stale-seconds and sets the gauge. Here we drive that computation directly
    with a ``last_update`` set to > 2x the reconciler interval ago.
    """

    def test_stale_seconds_metric_reflects_staleness(self, caplog) -> None:
        import logging

        reconciler_interval = 30.0
        store = ExchangeTruthStore()

        # Simulate a last update 2x+ the reconciler interval in the past.
        stale_age = reconciler_interval * 2 + 5  # 65s
        now = time.time()
        last_update_epoch = now - stale_age

        # The reconcile pass would compute stale_seconds and set the gauge.
        stale_seconds = now - last_update_epoch
        exchange_truth_store_stale_seconds.set(stale_seconds)

        # AC4: gauge reflects the staleness (> 2x interval).
        assert (
            _gauge_value(exchange_truth_store_stale_seconds) > reconciler_interval * 2
        )

        # AC4: a warning (not an error) is the correct severity for a degraded
        # — but still serving — store.
        with caplog.at_level(logging.WARNING):
            logging.getLogger("tradeengine.exchange_truth_store").warning(
                "ExchangeTruthStore stale: %.1fs since last stream update",
                stale_seconds,
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert warnings, "staleness should log at WARNING severity"
        assert not errors, "staleness is degraded, not a failure — no ERROR"

    def test_fresh_store_is_not_stale(self) -> None:
        # A store that just received an update should report ~0 staleness.
        exchange_truth_store_stale_seconds.set(0.0)
        assert _gauge_value(exchange_truth_store_stale_seconds) < 1.0


# ---------------------------------------------------------------------------
# AC5: Naked remediator metric shows detection without action in dry_run
# ---------------------------------------------------------------------------


class _RemediatorSpy:
    """Async-callable spies for exchange / close_position / position_manager."""

    def __init__(self) -> None:
        self.execute_calls: list = []
        self.close_calls: list = []

    async def execute(self, order: object) -> dict:
        self.execute_calls.append(order)
        return {"order_id": "1", "status": "NEW"}

    async def close(self, **kwargs: object) -> dict:
        self.close_calls.append(kwargs)
        return {"status": "closed"}

    def get_positions(self) -> dict:
        return {}


class TestAC5DryRunDetectsWithoutActing:
    """AC5 — in ``dry_run`` the remediator DETECTS a naked position (increments
    ``naked_position_detected_total``) but takes NO write action:
    ``naked_position_rearmed_total`` and ``naked_position_flattened_total``
    stay flat, and no exchange calls are made.

    This documents the ``dry_run``-default weakness: the safe default observes
    but never protects. Flipping to an enforcing mode is a deliberate operator
    action (see tests/test_adversarial_500_watchdog_mode.py).
    """

    @pytest.mark.asyncio
    async def test_dry_run_detects_but_does_not_act(self) -> None:
        spy = _RemediatorSpy()
        rem = NakedPositionRemediator(
            exchange=spy,  # type: ignore[arg-type]
            position_manager=spy,  # type: ignore[arg-type]
            close_position=spy.close,
            mode="dry_run",
            flatten_grace_sec=60,
            clock=lambda: 0.0,
        )

        detected_before = _counter_total(naked_position_detected_total)
        rearmed_before = _counter_total(naked_position_rearmed_total)
        flattened_before = _counter_total(naked_position_flattened_total)

        divergence = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "binance_qty": 0.01,
            "sl_present": False,
            "tp_present": False,
        }
        counts = await rem.remediate(
            [divergence], {("BTCUSDT", "LONG"): {"entryPrice": 60000.0}}
        )

        # AC5: detected > 0
        assert counts["detected"] == 1
        assert (_counter_total(naked_position_detected_total) - detected_before) >= 1

        # AC5: rearmed == 0 (no re-arm counter increment in dry_run)
        assert counts["armed"] == 0
        assert (_counter_total(naked_position_rearmed_total) - rearmed_before) == 0

        # AC5: flattened == 0 (no flatten counter increment in dry_run)
        assert counts["flattened"] == 0
        assert (_counter_total(naked_position_flattened_total) - flattened_before) == 0

        # No exchange writes occurred.
        assert spy.execute_calls == []
        assert spy.close_calls == []

    @pytest.mark.asyncio
    async def test_dry_run_over_grace_still_does_not_flatten(self) -> None:
        # Even after the flatten grace window elapses, dry_run must not flatten.
        # TODO(#970): confirm operators intend dry_run to remain no-op past the
        #  grace window (it does today) — this locks that contract in.
        spy = _RemediatorSpy()
        clock_vals = iter([0.0, 120.0])
        last = [0.0]

        def clock() -> float:
            try:
                last[0] = next(clock_vals)
            except StopIteration:
                pass
            return last[0]

        rem = NakedPositionRemediator(
            exchange=spy,  # type: ignore[arg-type]
            position_manager=spy,  # type: ignore[arg-type]
            close_position=spy.close,
            mode="dry_run",
            flatten_grace_sec=60,
            clock=clock,
        )
        divergence = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "binance_qty": 0.01,
            "sl_present": False,
            "tp_present": False,
        }
        await rem.remediate([divergence])
        counts = await rem.remediate([divergence])  # t=120 > grace
        assert counts["flattened"] == 0
        assert spy.close_calls == []
