"""Adversarial tests for tradeengine#500 — naked-position watchdog runs 'off' in prod.

The `NakedPositionRemediator` is constructed and started every boot, but in
production ``TE_NAKED_POSITION_REMEDIATION_MODE`` is unset so it defaults to
``off`` (shared/config.py:100). In ``off`` mode ``remediate()`` only increments
``naked_position_detected_total`` and takes NO write action — so real naked
positions are detected and then ignored (the 2026-07-16 free-for-all).

These tests lock in the mode-behavior contract:
  - off / dry_run       → NEVER re-arm or flatten (no exchange writes)
  - arm_only            → re-arms, never flattens
  - arm_or_flatten      → re-arms, and flattens after grace

They PASS today (they document the intended per-mode contract). The #500 fix is
a deployment/config change (set the env, surface the mode, alert on off) rather
than a code-logic change — so these serve as the regression guard ensuring the
enforcement code paths actually do work once the mode is flipped, and a
canary/config test (below) documents the prod-safety requirement.
"""

from __future__ import annotations

import pytest

from tradeengine.naked_position_remediator import NakedPositionRemediator


class _Rec:
    """Minimal async-callable spies for exchange/close/position_manager."""

    def __init__(self) -> None:
        self.execute_calls: list = []
        self.close_calls: list = []

    async def execute(self, order: object) -> dict:
        self.execute_calls.append(order)
        return {"order_id": "1000000000000001", "status": "NEW"}

    async def close(self, **kwargs: object) -> dict:
        self.close_calls.append(kwargs)
        return {"status": "closed"}

    def get_positions(self) -> dict:
        return {}


def _divergence() -> dict:
    return {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "binance_qty": 0.052,
        "sl_present": False,
        "tp_present": False,
    }


def _make(mode: str, clock_vals: list[float]) -> tuple[NakedPositionRemediator, _Rec]:
    rec = _Rec()
    it = iter(clock_vals)
    last = [clock_vals[0]]

    def clock() -> float:
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    rem = NakedPositionRemediator(
        exchange=rec,  # type: ignore[arg-type]
        position_manager=rec,  # type: ignore[arg-type]
        close_position=rec.close,
        mode=mode,  # type: ignore[arg-type]
        flatten_grace_sec=60,
        clock=clock,
    )
    return rem, rec


class TestOffModeIsNoOp:
    @pytest.mark.asyncio
    async def test_off_mode_never_writes(self) -> None:
        rem, rec = _make("off", [0.0, 0.0])
        counts = await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        assert counts["detected"] == 1
        assert counts["skipped"] == 1
        assert counts["armed"] == 0 and counts["flattened"] == 0
        assert rec.execute_calls == [] and rec.close_calls == []

    @pytest.mark.asyncio
    async def test_dry_run_never_writes(self) -> None:
        rem, rec = _make("dry_run", [0.0, 0.0])
        counts = await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        assert counts["detected"] == 1
        assert counts["skipped"] == 1
        assert rec.execute_calls == [] and rec.close_calls == []


class TestEnforcementModesDoAct:
    @pytest.mark.asyncio
    async def test_arm_only_rearms(self) -> None:
        rem, rec = _make("arm_only", [0.0, 0.0])
        counts = await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        assert counts["armed"] >= 1
        assert len(rec.execute_calls) >= 1  # SL and/or TP re-armed
        assert rec.close_calls == []  # arm_only never flattens

    @pytest.mark.asyncio
    async def test_arm_or_flatten_flattens_after_grace(self) -> None:
        # first_seen at t=0, then t=120 (> 60s grace) → flatten
        rem, rec = _make("arm_or_flatten", [0.0, 120.0])
        # First pass registers first_seen at t=0
        await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        # Second pass at t=120 → grace expired → flatten
        counts = await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        assert counts["flattened"] >= 1
        assert len(rec.close_calls) >= 1


class TestUnknownModeFailsSafe:
    @pytest.mark.asyncio
    async def test_unknown_mode_coerces_to_off(self) -> None:
        rem, rec = _make("garbage_mode", [0.0, 0.0])
        assert rem.mode == "off"
        counts = await rem.remediate(
            [_divergence()], {("ETHUSDT", "LONG"): {"entryPrice": 1876.0}}
        )
        assert rec.execute_calls == [] and rec.close_calls == []
        assert counts["skipped"] == 1


class TestProdConfigSafety:
    """The #500 fix requirement: prod must not silently run 'off'.

    Previously this was an ``xfail(strict=True)`` red-first guard (PR #506).
    The #500 fix flips ``shared/config.py`` default from ``off`` → ``dry_run``,
    so the assertion now passes: a fresh deploy with an unset
    ``TE_NAKED_POSITION_REMEDIATION_MODE`` is never silently detection-only.
    """

    def test_default_mode_is_not_silently_off(self) -> None:
        from shared.config import Settings

        mode = str(Settings().naked_position_remediation_mode).lower()
        assert mode != "off", (
            "Default naked_position_remediation_mode is 'off' — deploys run "
            "detection-only with no enforcement (#500)"
        )

    def test_default_mode_is_dry_run(self) -> None:
        """#500 AC: the safe default is exactly 'dry_run' — no writes on a
        fresh boot, but intended actions are logged/observable (not blind)."""
        from shared.config import Settings

        assert str(Settings().naked_position_remediation_mode).lower() == "dry_run"


class TestRemediationModeMetric:
    """#500 AC2: effective mode is exported as a metric for alerting."""

    def _series(self) -> dict[str, float]:
        from tradeengine.metrics import (
            _NAKED_REMEDIATION_MODES,
            naked_position_remediation_mode_status,
        )

        return {
            m: naked_position_remediation_mode_status.labels(mode=m)._value.get()
            for m in _NAKED_REMEDIATION_MODES
        }

    def test_active_mode_is_one_others_zero(self) -> None:
        from tradeengine.metrics import set_naked_position_remediation_mode

        set_naked_position_remediation_mode("arm_or_flatten")
        series = self._series()
        assert series["arm_or_flatten"] == 1
        assert series["off"] == 0
        assert series["dry_run"] == 0
        assert series["arm_only"] == 0

    def test_mode_transition_clears_previous(self) -> None:
        from tradeengine.metrics import set_naked_position_remediation_mode

        set_naked_position_remediation_mode("arm_only")
        set_naked_position_remediation_mode("dry_run")
        series = self._series()
        assert series["dry_run"] == 1
        # Previous mode must be cleared — no stale `1`.
        assert series["arm_only"] == 0

    def test_off_mode_surfaces_for_alerting(self) -> None:
        from tradeengine.metrics import set_naked_position_remediation_mode

        set_naked_position_remediation_mode("off")
        # The alert `{mode="off"} == 1` must fire on detection-only.
        assert self._series()["off"] == 1

    def test_unknown_mode_coerces_to_off(self) -> None:
        from tradeengine.metrics import set_naked_position_remediation_mode

        set_naked_position_remediation_mode("garbage")
        series = self._series()
        # Misconfigured env surfaces as unsafe 'off', never silently absent.
        assert series["off"] == 1
        assert sum(series.values()) == 1
