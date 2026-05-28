"""Tests for the halt-suspected detector (#419, FR66 extension)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from shared.constants import UTC
from tradeengine.services.halt_suspected_detector import HaltSuspectedDetector


class _StubPublisher:
    """Captures publish calls in-order so the test can assert exact emits."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(
        self,
        *,
        alert_name: str,
        severity: str,
        payload: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> bool:
        self.calls.append(
            {
                "alert_name": alert_name,
                "severity": severity,
                "payload": payload,
                "timestamp": timestamp,
            }
        )
        return True


class _ManualClock:
    def __init__(self, start: datetime) -> None:
        self.now_value = start

    def __call__(self) -> datetime:
        return self.now_value

    def advance(self, seconds: float) -> None:
        self.now_value = self.now_value + timedelta(seconds=seconds)


def _make_detector(
    *,
    window_seconds: int = 300,
    count_threshold: int = 10,
) -> tuple[HaltSuspectedDetector, _StubPublisher, _ManualClock]:
    publisher = _StubPublisher()
    clock = _ManualClock(datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC))
    detector = HaltSuspectedDetector(
        publisher=publisher,
        now=clock,
        window_seconds=window_seconds,
        count_threshold=count_threshold,
    )
    return detector, publisher, clock


@pytest.mark.asyncio
async def test_emit_when_count_threshold_exceeded_within_window():
    """AC7.a trigger A: >10 balance rejections inside the 5-min window."""

    detector, publisher, clock = _make_detector()

    # 11 balance rejections inside a 5-minute window must trip the threshold.
    for i in range(11):
        await detector.on_rejection(
            rejection_source="balance",
            decision_id=f"d-{i}",
        )
        clock.advance(10)

    halt_calls = [c for c in publisher.calls if c["alert_name"] == "halt_suspected"]
    assert len(halt_calls) == 1, publisher.calls
    assert halt_calls[0]["severity"] == "critical"
    payload = halt_calls[0]["payload"]
    assert payload["rejected_count"] == 11
    assert payload["last_10_decision_ids"] == [f"d-{i}" for i in range(1, 11)]
    assert detector.is_halt_active is True


@pytest.mark.asyncio
async def test_emit_when_window_duration_exceeded_with_all_rejected():
    """AC7.a trigger B: continuous rejections spanning more than the window."""

    detector, publisher, clock = _make_detector(
        window_seconds=300,
        count_threshold=999,  # disable trigger A so we exercise trigger B
    )

    # 6 rejections spread across >5 minutes, with no successful completion in between.
    await detector.on_rejection(rejection_source="balance", decision_id="d-0")
    for i in range(1, 7):
        clock.advance(60)  # one minute apart
        await detector.on_rejection(rejection_source="balance", decision_id=f"d-{i}")

    halt_calls = [c for c in publisher.calls if c["alert_name"] == "halt_suspected"]
    assert len(halt_calls) == 1, publisher.calls
    assert detector.is_halt_active is True


@pytest.mark.asyncio
async def test_non_balance_completion_clears_halt_state():
    """AC7.c: a non-balance-rejected completion emits halt_cleared and resets state."""

    detector, publisher, clock = _make_detector()

    for i in range(11):
        await detector.on_rejection(
            rejection_source="balance",
            decision_id=f"d-{i}",
        )
        clock.advance(5)

    assert detector.is_halt_active is True

    # A fill arrives — emits halt_cleared and resets the ledger.
    clock.advance(5)
    await detector.on_completion()

    cleared_calls = [c for c in publisher.calls if c["alert_name"] == "halt_cleared"]
    assert len(cleared_calls) == 1
    assert cleared_calls[0]["severity"] == "info"
    assert detector.is_halt_active is False
    assert detector.tracked_rejection_count == 0


@pytest.mark.asyncio
async def test_repeat_threshold_crossings_dedup_until_cleared():
    """While halt is active, subsequent threshold crossings must not re-emit."""

    detector, publisher, clock = _make_detector()

    for i in range(15):
        await detector.on_rejection(
            rejection_source="balance",
            decision_id=f"d-{i}",
        )
        clock.advance(5)

    halt_calls = [c for c in publisher.calls if c["alert_name"] == "halt_suspected"]
    assert len(halt_calls) == 1, "halt_suspected must dedup until cleared"


@pytest.mark.asyncio
async def test_non_balance_rejection_resets_ledger_without_emitting_cleared():
    """A non-balance rejection on a *cold* detector resets the ledger silently."""

    detector, publisher, clock = _make_detector()

    # Three balance rejections, not enough to trigger.
    for i in range(3):
        await detector.on_rejection(rejection_source="balance", decision_id=f"d-{i}")
        clock.advance(5)

    # A risk_check rejection arrives — counts as non-balance, resets ledger.
    await detector.on_rejection(rejection_source="risk_check", decision_id="d-risk")

    assert detector.tracked_rejection_count == 0
    # halt was never active, so no halt_cleared either.
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_below_both_thresholds_does_not_emit():
    """A short burst that misses both triggers must stay silent.

    5 balance rejections spread across 240 s: count is well under
    ``count_threshold=10`` AND the run is shorter than
    ``window_seconds=300``, so neither trigger A nor trigger B fires.
    """

    detector, publisher, clock = _make_detector(
        window_seconds=300,
        count_threshold=10,
    )

    for i in range(5):
        await detector.on_rejection(rejection_source="balance", decision_id=f"d-{i}")
        clock.advance(60)

    halt_calls = [c for c in publisher.calls if c["alert_name"] == "halt_suspected"]
    assert halt_calls == []
    assert detector.is_halt_active is False


@pytest.mark.asyncio
async def test_halt_payload_carries_last_10_decision_ids():
    """AC7.b: payload includes the most recent 10 balance-rejected decision_ids."""

    detector, publisher, clock = _make_detector()

    for i in range(15):
        await detector.on_rejection(rejection_source="balance", decision_id=f"d-{i}")
        clock.advance(2)

    halt_calls = [c for c in publisher.calls if c["alert_name"] == "halt_suspected"]
    assert len(halt_calls) == 1
    payload = halt_calls[0]["payload"]
    # The first trigger fires at the 11th rejection (i=10), so last_10
    # at trigger time is d-1..d-10.
    assert payload["last_10_decision_ids"] == [f"d-{i}" for i in range(1, 11)]
