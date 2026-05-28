"""Unit tests for TradeEngineHealthEvaluator (#417, P2.7 AC5).

Note: several other test modules in this repo install
``sys.modules["petrosa_otel"] = MagicMock()`` at module-import time
(test_consumer.py, test_nats_trace_propagation.py, test_api_lifespan_integration.py,
test_issue_355_exchange_failed_status.py). To avoid colliding with that global
pollution under any collection order, this module performs its
``petrosa_otel.evaluators`` imports **inside the test bodies** (after the
autouse fixture below has restored the real package), never at module top.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _real_petrosa_otel():
    """Save any mock stub, force-load the real package, restore on teardown."""
    saved = {k: v for k, v in sys.modules.items() if k.startswith("petrosa_otel")}
    for key in list(saved):
        del sys.modules[key]
    importlib.import_module("petrosa_otel")
    importlib.import_module("petrosa_otel.evaluators")
    yield
    for key in list(sys.modules):
        if (
            key.startswith("petrosa_otel")
            or key == "tradeengine.evaluators"
            or key.startswith("tradeengine.evaluators.")
        ):
            del sys.modules[key]
    sys.modules.update(saved)


class FakeClock:
    def __init__(self, start: datetime, step: timedelta) -> None:
        self._t = start
        self._step = step

    def __call__(self) -> datetime:
        now = self._t
        self._t = self._t + self._step
        return now


class MetricsSource:
    def __init__(self) -> None:
        self.snap: dict[str, Any] = {
            "risk_checks": 0.0,
            "risk_rejections": 0.0,
            "order_latency_sum": 0.0,
            "order_latency_count": 0.0,
            "divergences": 0.0,
        }

    def __call__(self) -> dict[str, Any]:
        return dict(self.snap)


class FakeNats:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.messages.append((subject, payload))


def _make(source, clock, *, publisher=None, n: int = 1):
    from petrosa_otel.evaluators import ConsecutiveSamplesHysteresis

    from tradeengine.evaluators import TradeEngineHealthEvaluator

    return TradeEngineHealthEvaluator(
        metrics_source=source,
        publisher=publisher,
        hysteresis=ConsecutiveSamplesHysteresis(n=n),
        time_source=clock,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 5, 28, 0, 0, 0, tzinfo=UTC), timedelta(seconds=15))


@pytest.mark.asyncio
async def test_first_sample_is_unknown(clock):
    ev = _make(MetricsSource(), clock)
    verdict, reason = await ev.evaluate()
    assert verdict == "unknown"
    assert "baseline" in reason.lower()


@pytest.mark.asyncio
async def test_healthy_when_metrics_progress_within_thresholds(clock):
    src = MetricsSource()
    ev = _make(src, clock)
    await ev.evaluate()
    src.snap.update(
        {
            "risk_checks": 10.0,
            "risk_rejections": 1.0,
            "order_latency_sum": 5.0,  # 10 orders @ 0.5s
            "order_latency_count": 10.0,
        }
    )
    verdict, reason = await ev.evaluate()
    assert verdict == "healthy"
    assert "checks 10" in reason


@pytest.mark.asyncio
async def test_unhealthy_on_position_divergence(clock):
    src = MetricsSource()
    ev = _make(src, clock)
    await ev.evaluate()
    src.snap["divergences"] = 3.0
    verdict, reason = await ev.evaluate()
    assert verdict == "unhealthy"
    assert "divergence" in reason


@pytest.mark.asyncio
async def test_unhealthy_on_high_order_latency(clock):
    src = MetricsSource()
    ev = _make(src, clock)
    await ev.evaluate()
    # 5 orders averaging 15s — above the 10s threshold.
    src.snap.update({"order_latency_sum": 75.0, "order_latency_count": 5.0})
    verdict, reason = await ev.evaluate()
    assert verdict == "unhealthy"
    assert "latency" in reason


@pytest.mark.asyncio
async def test_unhealthy_on_zero_pre_trade_pass_rate(clock):
    """The #404 scenario: every pre-trade check rejected."""
    src = MetricsSource()
    ev = _make(src, clock)
    await ev.evaluate()
    src.snap.update({"risk_checks": 10.0, "risk_rejections": 10.0})
    verdict, reason = await ev.evaluate()
    assert verdict == "unhealthy"
    assert "pass rate 0%" in reason


@pytest.mark.asyncio
async def test_rejection_check_dormant_below_volume(clock):
    """A handful of rejections with low check volume must not trip the verdict."""
    src = MetricsSource()
    ev = _make(src, clock)
    await ev.evaluate()
    src.snap.update({"risk_checks": 2.0, "risk_rejections": 2.0})
    verdict, _ = await ev.evaluate()
    assert verdict == "healthy"


@pytest.mark.asyncio
async def test_counter_reset_returns_unknown(clock):
    src = MetricsSource()
    ev = _make(src, clock)
    src.snap["risk_checks"] = 500.0
    await ev.evaluate()
    src.snap["risk_checks"] = 5.0  # pod restart resets the counter
    verdict, reason = await ev.evaluate()
    assert verdict == "unknown"
    assert "reset" in reason.lower()


@pytest.mark.asyncio
async def test_publishes_on_tradeengine_subject(clock):
    from petrosa_otel.evaluators.publisher import (
        EVALUATOR_SUBJECT_TEMPLATE,
        NatsVerdictPublisher,
    )

    src = MetricsSource()
    nats = FakeNats()
    ev = _make(src, clock, publisher=NatsVerdictPublisher(nats_client=nats), n=1)

    await ev.tick()  # unknown baseline
    src.snap.update({"risk_checks": 5.0, "order_latency_count": 5.0})
    await ev.tick()  # healthy

    assert nats.messages, "evaluator did not publish"
    subject, payload = nats.messages[-1]
    assert subject == "evaluator.tradeengine.verdict"
    assert subject == EVALUATOR_SUBJECT_TEMPLATE.format(subsystem="tradeengine")
    body = json.loads(payload.decode())
    assert body["subsystem"] == "tradeengine"
    assert body["verdict"] == "healthy"


@pytest.mark.asyncio
async def test_hysteresis_suppresses_single_flap(clock):
    src = MetricsSource()
    ev = _make(src, clock, n=3)

    await ev.tick()  # unknown baseline
    for _ in range(3):
        src.snap["risk_checks"] = src.snap["risk_checks"] + 5.0
        v = await ev.tick()
    assert v.verdict == "healthy"

    # One divergence sample must NOT flip the committed verdict (n=3).
    src.snap["divergences"] = src.snap["divergences"] + 1.0
    v = await ev.tick()
    assert v.verdict == "healthy"


@pytest.mark.asyncio
async def test_start_stop_without_nats_does_not_crash(clock):
    src = MetricsSource()
    ev = _make(src, clock, n=1)
    await ev.start()
    await asyncio.sleep(0.01)
    await ev.stop()
    await ev.stop()  # idempotent


def test_build_returns_none_when_nats_disabled():
    from tradeengine.evaluators import build_tradeengine_health_evaluator

    assert build_tradeengine_health_evaluator(nats_servers=None) is None
    assert build_tradeengine_health_evaluator(nats_servers="") is None


def test_counter_total_aggregates_labels():
    from tradeengine.evaluators.health_evaluator import (
        _counter_total,
        _histogram_sum_count,
    )

    class _Family:
        def __init__(self, samples):
            self.samples = samples

    class _Sample:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class _Metric:
        def __init__(self, samples):
            self._samples = samples

        def collect(self):
            return [_Family(self._samples)]

    counter = _Metric(
        [
            _Sample("foo_total", 3.0),
            _Sample("foo_total", 7.0),
            _Sample("foo_created", 0.0),  # ignored
        ]
    )
    assert _counter_total(counter) == 10.0

    histogram = _Metric(
        [
            _Sample("foo_sum", 12.5),
            _Sample("foo_count", 5.0),
            _Sample("foo_sum", 2.5),
            _Sample("foo_count", 1.0),
            _Sample("foo_bucket", 99.0),  # ignored
        ]
    )
    assert _histogram_sum_count(histogram) == (15.0, 6.0)
