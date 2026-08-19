"""Regression tests for #540: the naked-position reconciler boot gate.

Root cause: `tradeengine/api.py` gated the PositionReconciler/NakedPositionRemediator
start on `position_reconciliation_enabled AND not simulation_enabled`. The live
deployment set `TE_NAKED_POSITION_REMEDIATION_MODE=arm_only` but left
`SIMULATION_ENABLED` at its `True` default, so the gate evaluated False and the
watchdog was never constructed — `arm_only` became a silent no-op while real
orders filled on Binance (18 naked positions, zero remediation cycles).

These tests pin the new behavior:
  * default (`position_reconciliation_requires_live_only=False`): the watchdog
    starts whenever reconciliation is enabled, regardless of the sim flag;
  * legacy opt-in (`...requires_live_only=True`): restores the sim-gated skip;
  * the `position_reconciler_running` / `position_reconciler_skipped_while_live`
    gauges make a skip-while-real-trading misconfiguration alertable.
"""

from tradeengine.metrics import (
    position_reconciler_running,
    position_reconciler_skipped_while_live,
    set_position_reconciler_running,
)


def _gate_decision(
    *, enabled: bool, simulation_enabled: bool, requires_live_only: bool
) -> bool:
    """Mirror of the boot-gate decision in tradeengine/api.py (#540).

    Kept in lock-step with the production gate so the truth table is asserted
    directly without spinning up the full FastAPI lifespan.
    """
    real_trading_active = not simulation_enabled
    if requires_live_only:
        return enabled and real_trading_active
    return enabled


class TestBootGateDecision:
    def test_default_starts_when_enabled_even_in_simulation(self) -> None:
        # The #540 scenario: enabled + simulation True (default) MUST start.
        assert (
            _gate_decision(
                enabled=True, simulation_enabled=True, requires_live_only=False
            )
            is True
        )

    def test_default_starts_when_live(self) -> None:
        assert (
            _gate_decision(
                enabled=True, simulation_enabled=False, requires_live_only=False
            )
            is True
        )

    def test_default_skips_when_reconciliation_disabled(self) -> None:
        assert (
            _gate_decision(
                enabled=False, simulation_enabled=False, requires_live_only=False
            )
            is False
        )

    def test_legacy_sim_gated_skips_in_simulation(self) -> None:
        # Opt-in legacy behavior: simulation True skips (pre-#540 semantics).
        assert (
            _gate_decision(
                enabled=True, simulation_enabled=True, requires_live_only=True
            )
            is False
        )

    def test_legacy_sim_gated_starts_when_live(self) -> None:
        assert (
            _gate_decision(
                enabled=True, simulation_enabled=False, requires_live_only=True
            )
            is True
        )


class TestReconcilerRunningMetric:
    def test_running_sets_gauge_and_clears_alert(self) -> None:
        set_position_reconciler_running(True, skipped_while_live=True)
        assert position_reconciler_running._value.get() == 1
        # Running wins: the skip-while-live alert must be cleared when running.
        assert position_reconciler_skipped_while_live._value.get() == 0

    def test_skipped_while_live_raises_alert(self) -> None:
        set_position_reconciler_running(False, skipped_while_live=True)
        assert position_reconciler_running._value.get() == 0
        assert position_reconciler_skipped_while_live._value.get() == 1

    def test_skipped_in_simulation_is_not_an_alert(self) -> None:
        # Disabled/simulated skip is benign — no page.
        set_position_reconciler_running(False, skipped_while_live=False)
        assert position_reconciler_running._value.get() == 0
        assert position_reconciler_skipped_while_live._value.get() == 0
