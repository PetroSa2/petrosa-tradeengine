"""Unit tests for NakedPositionRemediator (#445).

Covers all four modes (off, dry_run, arm_only, arm_or_flatten),
grace-window flatten escalation, idempotent re-arm, fallback SL/TP
derivation, and clean-pass first-seen reset.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.naked_position_remediator import NakedPositionRemediator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unhedged_div(
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    qty: float = 0.5,
    sl_present: bool = False,
    tp_present: bool = False,
) -> dict[str, Any]:
    return {
        "category": "unhedged",
        "symbol": symbol,
        "side": side,
        "binance_qty": qty,
        "local_qty": 0.0,
        "sl_present": sl_present,
        "tp_present": tp_present,
        "detail": "test fixture",
    }


def _binance_positions(
    symbol: str = "BTCUSDT", side: str = "LONG", entry: float = 50000.0
):
    return {
        (symbol, side): {
            "symbol": symbol,
            "positionSide": side,
            "positionAmt": 0.5,
            "entryPrice": entry,
        }
    }


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _make_remediator(
    *,
    mode: str = "off",
    grace_sec: int = 60,
    position_manager_positions: dict | None = None,
    exchange_execute_raises: bool = False,
    close_return: dict | None = None,
    close_raises: bool = False,
    fallback_sl_pct: float = 2.0,
    fallback_tp_pct: float = 4.0,
    min_sl_distance_pct: float = 0.0,
    max_consecutive_arm_failures: int = 5,
    arm_backoff_cooldown_sec: int = 300,
) -> tuple[NakedPositionRemediator, MagicMock, MagicMock, AsyncMock, _FakeClock]:
    exchange = MagicMock()
    if exchange_execute_raises:
        exchange.execute = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        exchange.execute = AsyncMock(return_value={"status": "FILLED"})

    pm = MagicMock()
    pm.get_positions = MagicMock(return_value=position_manager_positions or {})

    if close_raises:
        close_cb = AsyncMock(side_effect=RuntimeError("close failed"))
    else:
        close_cb = AsyncMock(
            return_value=close_return or {"status": "success", "position_closed": True}
        )

    clock = _FakeClock()
    remediator = NakedPositionRemediator(
        exchange=exchange,
        position_manager=pm,
        close_position=close_cb,
        mode=mode,  # type: ignore[arg-type]
        flatten_grace_sec=grace_sec,
        fallback_sl_pct=fallback_sl_pct,
        fallback_tp_pct=fallback_tp_pct,
        min_sl_distance_pct=min_sl_distance_pct,
        max_consecutive_arm_failures=max_consecutive_arm_failures,
        arm_backoff_cooldown_sec=arm_backoff_cooldown_sec,
        clock=clock,
    )
    return remediator, exchange, pm, close_cb, clock


# ---------------------------------------------------------------------------
# Mode coercion + property
# ---------------------------------------------------------------------------


def test_unknown_mode_falls_back_to_off() -> None:
    r, _, _, _, _ = _make_remediator(mode="not_a_real_mode")
    assert r.mode == "off"


@pytest.mark.parametrize("m", ["off", "dry_run", "arm_only", "arm_or_flatten"])
def test_valid_modes_round_trip(m: str) -> None:
    r, _, _, _, _ = _make_remediator(mode=m)
    assert r.mode == m


# ---------------------------------------------------------------------------
# off mode — never writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_mode_never_calls_exchange_or_close() -> None:
    r, ex, _, close_cb, _ = _make_remediator(mode="off")
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["detected"] == 1
    assert counts["skipped"] == 1
    assert counts["armed"] == 0
    assert counts["flattened"] == 0
    ex.execute.assert_not_called()
    close_cb.assert_not_called()


# ---------------------------------------------------------------------------
# dry_run mode — logs only, no writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_mode_logs_no_writes() -> None:
    r, ex, _, close_cb, _ = _make_remediator(mode="dry_run")
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["detected"] == 1
    assert counts["skipped"] == 1
    ex.execute.assert_not_called()
    close_cb.assert_not_called()


# ---------------------------------------------------------------------------
# arm_only mode — re-arms via exchange.execute, never flattens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_only_places_sl_and_tp_with_fallback_prices() -> None:
    r, ex, _, close_cb, _ = _make_remediator(mode="arm_only")
    counts = await r.remediate([_unhedged_div()], _binance_positions(entry=50000.0))
    assert counts["armed"] == 1
    assert counts["flattened"] == 0
    # SL + TP = 2 calls
    assert ex.execute.call_count == 2
    close_cb.assert_not_called()
    # Verify SL price = entry * (1 - 2%) for LONG default fallback
    sl_call = ex.execute.call_args_list[0]
    sl_order = sl_call.args[0] if sl_call.args else sl_call.kwargs.get("order")
    assert sl_order.symbol == "BTCUSDT"
    assert sl_order.type == "stop"
    assert sl_order.position_side == "LONG"


@pytest.mark.asyncio
async def test_arm_only_never_flattens_even_past_grace() -> None:
    r, ex, _, close_cb, clock = _make_remediator(mode="arm_only", grace_sec=60)
    # First pass — re-arm attempt
    await r.remediate([_unhedged_div()], _binance_positions())
    # Advance way past grace
    clock.advance(3600)
    # Position still unhedged — arm_only must still try arm, not flatten
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["flattened"] == 0
    close_cb.assert_not_called()
    assert ex.execute.call_count >= 2  # at least one re-arm pass each cycle


@pytest.mark.asyncio
async def test_arm_only_uses_local_strategy_sl_tp_prices() -> None:
    local_positions = {
        ("BTCUSDT", "LONG"): {
            "stop_loss_price": 48500.0,
            "take_profit_price": 52500.0,
            "position_id": "strat-1",
        }
    }
    r, ex, _, _, _ = _make_remediator(
        mode="arm_only", position_manager_positions=local_positions
    )
    await r.remediate([_unhedged_div()], _binance_positions(entry=50000.0))
    # Should use 48500 / 52500 not the 2% fallback (49000 / 52000)
    sl_call_args = ex.execute.call_args_list[0]
    sl_order = sl_call_args.args[0]
    assert sl_order.stop_loss == 48500.0
    tp_call_args = ex.execute.call_args_list[1]
    tp_order = tp_call_args.args[0]
    assert tp_order.take_profit == 52500.0


@pytest.mark.asyncio
async def test_arm_only_partial_arm_when_tp_already_present() -> None:
    r, ex, _, _, _ = _make_remediator(mode="arm_only")
    div = _unhedged_div(sl_present=False, tp_present=True)
    counts = await r.remediate([div], _binance_positions())
    # Only SL placement attempted
    assert ex.execute.call_count == 1
    assert counts["armed"] == 1


@pytest.mark.asyncio
async def test_arm_only_failure_counts_failed() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only", exchange_execute_raises=True)
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["failed"] == 1
    assert counts["armed"] == 0


# ---------------------------------------------------------------------------
# arm_or_flatten mode — grace-window escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_or_flatten_first_pass_arms_does_not_flatten() -> None:
    r, ex, _, close_cb, _ = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["armed"] == 1
    assert counts["flattened"] == 0
    close_cb.assert_not_called()
    ex.execute.assert_called()


@pytest.mark.asyncio
async def test_arm_or_flatten_escalates_to_flatten_after_grace() -> None:
    r, _, _, close_cb, clock = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    # Pass 1: first-seen recorded, arm attempted
    await r.remediate([_unhedged_div()], _binance_positions())
    # Advance past grace
    clock.advance(61)
    # Pass 2: still unhedged — flatten kicks in
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["flattened"] == 1
    close_cb.assert_awaited_once()
    call = close_cb.await_args
    assert call.kwargs["symbol"] == "BTCUSDT"
    assert call.kwargs["position_side"] == "LONG"
    assert call.kwargs["quantity"] == pytest.approx(0.5)
    assert call.kwargs["reason"] == "naked_position_grace_expired"


@pytest.mark.asyncio
async def test_arm_or_flatten_flatten_failure_counts_failed() -> None:
    r, _, _, close_cb, clock = _make_remediator(
        mode="arm_or_flatten", grace_sec=60, close_raises=True
    )
    await r.remediate([_unhedged_div()], _binance_positions())
    clock.advance(61)
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["failed"] == 1
    assert counts["flattened"] == 0


# ---------------------------------------------------------------------------
# Idempotency / state lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_pass_clears_first_seen_state() -> None:
    r, _, _, _, clock = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    await r.remediate([_unhedged_div()], _binance_positions())
    # Clean pass — empty divergences
    counts_clean = await r.remediate([], None)
    assert counts_clean["detected"] == 0
    # Reappears later — should be treated as first-seen NOW, not escalate
    clock.advance(3600)
    counts = await r.remediate([_unhedged_div()], _binance_positions())
    assert counts["armed"] == 1
    assert counts["flattened"] == 0


@pytest.mark.asyncio
async def test_resolved_key_drops_from_first_seen() -> None:
    r, _, _, _, clock = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    div_btc = _unhedged_div(symbol="BTCUSDT")
    div_eth = _unhedged_div(symbol="ETHUSDT")
    await r.remediate(
        [div_btc, div_eth],
        {
            ("BTCUSDT", "LONG"): {"entryPrice": 50000.0, "positionAmt": 0.5},
            ("ETHUSDT", "LONG"): {"entryPrice": 3000.0, "positionAmt": 1.0},
        },
    )
    # ETH gets re-armed (no longer unhedged); BTC still unhedged next pass
    clock.advance(30)
    await r.remediate([div_btc], _binance_positions("BTCUSDT", "LONG", 50000.0))
    # ETH key should be dropped from internal state
    assert ("ETHUSDT", "LONG") not in r._first_seen
    assert ("BTCUSDT", "LONG") in r._first_seen


# ---------------------------------------------------------------------------
# Fallback price derivation
# ---------------------------------------------------------------------------


def test_derive_uses_fallback_for_long_when_no_local_record() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only")
    sl, tp, _ = r._derive_protective_prices(
        "BTCUSDT", "LONG", _binance_positions(entry=100.0)
    )
    assert sl == pytest.approx(98.0)  # 100 * (1 - 2%)
    assert tp == pytest.approx(104.0)  # 100 * (1 + 4%)


def test_derive_uses_fallback_for_short_when_no_local_record() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only")
    sl, tp, _ = r._derive_protective_prices(
        "BTCUSDT", "SHORT", _binance_positions("BTCUSDT", "SHORT", 100.0)
    )
    assert sl == pytest.approx(102.0)  # 100 * (1 + 2%)
    assert tp == pytest.approx(96.0)  # 100 * (1 - 4%)


def test_derive_returns_none_for_missing_entry_price() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only")
    sl, tp, _ = r._derive_protective_prices(
        "BTCUSDT", "LONG", {("BTCUSDT", "LONG"): {"entryPrice": 0}}
    )
    assert sl is None and tp is None


# ---------------------------------------------------------------------------
# Second-wave OCO-orphan fix: SL clamped to safety floor (C1 + C2)
# ---------------------------------------------------------------------------


def test_derive_widens_too_tight_stored_long_sl_to_floor() -> None:
    """C2: a stored LONG SL tighter than the floor is widened out.

    entry=100, floor=6% → LONG SL must be <= 94.0. A stored 97.6 (2.4%)
    is un-armable, so it is widened down to 94.0.
    """
    r, _, _, _, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=6.0)
    positions = _binance_positions("BTCUSDT", "LONG", 100.0)
    positions[("BTCUSDT", "LONG")]["stop_loss_price"] = 97.6
    r._position_manager.get_positions = MagicMock(
        return_value={("BTCUSDT", "LONG"): {"stop_loss_price": 97.6}}
    )
    sl, _, _ = r._derive_protective_prices("BTCUSDT", "LONG", positions)
    assert sl == pytest.approx(94.0)  # widened to floor, not left at 97.6


def test_derive_widens_too_tight_stored_short_sl_to_floor() -> None:
    """C2: a stored SHORT SL tighter than the floor is widened up.

    entry=100, floor=6% → SHORT SL must be >= 106.0. A stored 102.4 (2.4%)
    is un-armable, so it is widened up to 106.0.
    """
    r, _, _, _, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=6.0)
    positions = _binance_positions("BTCUSDT", "SHORT", 100.0)
    r._position_manager.get_positions = MagicMock(
        return_value={("BTCUSDT", "SHORT"): {"stop_loss_price": 102.4}}
    )
    sl, _, _ = r._derive_protective_prices("BTCUSDT", "SHORT", positions)
    assert sl == pytest.approx(106.0)


def test_derive_leaves_compliant_stored_sl_untouched() -> None:
    """A stored SL already outside the floor is preserved (strategy intent)."""
    r, _, _, _, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=6.0)
    positions = _binance_positions("BTCUSDT", "LONG", 100.0)
    r._position_manager.get_positions = MagicMock(
        return_value={("BTCUSDT", "LONG"): {"stop_loss_price": 90.0}}  # 10% away
    )
    sl, _, _ = r._derive_protective_prices("BTCUSDT", "LONG", positions)
    assert sl == pytest.approx(90.0)


def test_derive_fallback_sl_clears_floor_when_configured() -> None:
    """C1: a fallback SL below the floor is widened out to the floor.

    With the old 2% fallback and a 6% floor, the fallback SL (98.0 LONG)
    is un-armable; the clamp widens it to 94.0.
    """
    r, _, _, _, _ = _make_remediator(
        mode="arm_only", fallback_sl_pct=2.0, min_sl_distance_pct=6.0
    )
    sl, _, _ = r._derive_protective_prices(
        "BTCUSDT", "LONG", _binance_positions("BTCUSDT", "LONG", 100.0)
    )
    assert sl == pytest.approx(94.0)


def test_derive_no_clamp_when_floor_disabled() -> None:
    """min_sl_distance_pct=0 disables the clamp (legacy behaviour)."""
    r, _, _, _, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=0.0)
    r._position_manager.get_positions = MagicMock(
        return_value={("BTCUSDT", "LONG"): {"stop_loss_price": 99.0}}
    )
    sl, _, _ = r._derive_protective_prices(
        "BTCUSDT", "LONG", _binance_positions("BTCUSDT", "LONG", 100.0)
    )
    assert sl == pytest.approx(99.0)  # left tight, not widened


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_divergence_list_is_no_op_in_all_modes() -> None:
    for m in ["off", "dry_run", "arm_only", "arm_or_flatten"]:
        r, ex, _, close_cb, _ = _make_remediator(mode=m)
        counts = await r.remediate([], None)
        assert counts == {
            "detected": 0,
            "armed": 0,
            "flattened": 0,
            "skipped": 0,
            "failed": 0,
        }
    ex.execute.assert_not_called()
    close_cb.assert_not_called()


# ---------------------------------------------------------------------------
# #551 — market-crossed-entry SL must not re-arm a guaranteed -2021 price.
# ---------------------------------------------------------------------------


def _binance_positions_with_mark(
    symbol: str, side: str, entry: float, mark: float
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (symbol, side): {
            "symbol": symbol,
            "positionSide": side,
            "positionAmt": 1.0 if side == "LONG" else -1.0,
            "entryPrice": entry,
            "markPrice": mark,
        }
    }


def test_derive_reanchors_short_sl_to_correct_side_of_market() -> None:
    """XLMUSDT SHORT: entry 0.1637, mark risen to 0.171. Entry-floored SL
    (0.1637*1.06=0.1735) is still BELOW mark → would immediately trigger.
    Must re-anchor ABOVE mark (0.171*1.06)."""
    r, _, _, _, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=6.0)
    positions = _binance_positions_with_mark("XLMUSDT", "SHORT", 0.16370, 0.17100)
    sl, _tp, must_flatten = r._derive_protective_prices("XLMUSDT", "SHORT", positions)
    assert must_flatten is False
    assert sl is not None and sl > 0.17100, "SHORT SL must sit above live mark"
    assert sl == pytest.approx(0.17100 * (1 + 0.06))


def test_derive_signals_flatten_when_no_placeable_stop() -> None:
    """Pathological floor >= max placeable band → no stop can avoid immediate
    trigger; must_flatten is signalled instead of manufacturing a dead price."""
    r, _, _, _, _ = _make_remediator(mode="arm_or_flatten", min_sl_distance_pct=20.0)
    positions = _binance_positions_with_mark("XLMUSDT", "SHORT", 0.16370, 0.17100)
    _sl, _tp, must_flatten = r._derive_protective_prices("XLMUSDT", "SHORT", positions)
    assert must_flatten is True


@pytest.mark.asyncio
async def test_rearm_escalates_to_flatten_when_unplaceable_in_arm_or_flatten() -> None:
    """When no placeable stop exists, arm_or_flatten flattens instead of
    re-arming the identical -2021 price (the observed naked-forever loop)."""
    r, ex, _, close_cb, _ = _make_remediator(
        mode="arm_or_flatten", grace_sec=9999, min_sl_distance_pct=20.0
    )
    positions = _binance_positions_with_mark("XLMUSDT", "SHORT", 0.16370, 0.17100)
    div = _unhedged_div("XLMUSDT", "SHORT", qty=100.0)
    # grace not expired (9999s) — normally this is the arm path, but the
    # unplaceable-stop escalation flattens regardless of grace.
    counts = await r.remediate([div], positions)
    close_cb.assert_awaited_once()
    ex.execute.assert_not_called()  # never ships the guaranteed-dead SL
    assert counts["failed"] == 0


@pytest.mark.asyncio
async def test_rearm_does_not_retry_dead_price_in_arm_only() -> None:
    """arm_only cannot flatten, but must still NOT re-arm a guaranteed -2021
    price — it records a failure so operators see the stuck position."""
    r, ex, _, close_cb, _ = _make_remediator(mode="arm_only", min_sl_distance_pct=20.0)
    positions = _binance_positions_with_mark("XLMUSDT", "SHORT", 0.16370, 0.17100)
    div = _unhedged_div("XLMUSDT", "SHORT", qty=100.0)
    counts = await r.remediate([div], positions)
    ex.execute.assert_not_called()
    close_cb.assert_not_called()
    assert counts["failed"] == 1


@pytest.mark.asyncio
async def test_zero_qty_divergence_does_not_arm_or_flatten() -> None:
    r, ex, _, close_cb, clock = _make_remediator(mode="arm_or_flatten", grace_sec=0)
    div = _unhedged_div(qty=0.0)
    counts = await r.remediate([div], _binance_positions())
    assert counts["armed"] == 0
    assert counts["flattened"] == 0
    assert counts["failed"] == 1
    ex.execute.assert_not_called()
    close_cb.assert_not_called()


# ---------------------------------------------------------------------------
# #547 — malformed (inverted-sign) position routing
# ---------------------------------------------------------------------------


def _malformed_div(
    symbol: str = "LTCUSDT",
    side: str = "LONG",
    qty: float = 0.303,
    raw_amt: float = -0.303,
) -> dict[str, Any]:
    return {
        "category": "malformed_position",
        "symbol": symbol,
        "side": side,
        "binance_qty": qty,
        "raw_position_amt": raw_amt,
        "local_qty": 0.0,
        "sl_present": False,
        "tp_present": False,
        "detail": "test malformed fixture",
    }


@pytest.mark.asyncio
async def test_malformed_arm_only_never_arms_and_counts_metric() -> None:
    """AC3: arm_only must NOT emit a guaranteed-to-fail arm for a malformed
    position; it increments the malformed metric and records skipped."""
    from tradeengine import naked_position_remediator as npr

    r, ex, _, close_cb, _ = _make_remediator(mode="arm_only")
    with patch.object(npr, "malformed_position_total") as mock_metric:
        counts = await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    ex.execute.assert_not_called()  # never attempts a direction-invalid arm
    close_cb.assert_not_called()  # arm_only cannot flatten
    assert counts["armed"] == 0
    assert counts["flattened"] == 0
    assert counts["skipped"] == 1
    mock_metric.labels.assert_any_call(symbol="LTCUSDT", side="LONG")


@pytest.mark.asyncio
async def test_malformed_arm_only_alerts_once_per_episode() -> None:
    """AC3: CRITICAL log fires once per detection episode, not every cycle."""
    from tradeengine import naked_position_remediator as npr

    r, _, _, _, clock = _make_remediator(mode="arm_only")
    with patch.object(npr.logger, "critical") as mock_crit:
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        clock.advance(30)
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    assert mock_crit.call_count == 1


@pytest.mark.asyncio
async def test_malformed_arm_or_flatten_flattens_after_grace() -> None:
    """AC2: arm_or_flatten flattens a malformed position after grace, calling
    close_position with reason='malformed_position'."""
    r, ex, _, close_cb, clock = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    # Pass 1: pre-grace — alert only, no flatten, no arm.
    counts1 = await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    assert counts1["flattened"] == 0
    close_cb.assert_not_called()
    ex.execute.assert_not_called()
    # Pass 2: past grace — flatten.
    clock.advance(61)
    counts2 = await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    assert counts2["flattened"] == 1
    close_cb.assert_awaited_once()
    call = close_cb.await_args
    assert call.kwargs["symbol"] == "LTCUSDT"
    assert call.kwargs["position_side"] == "LONG"
    assert call.kwargs["quantity"] == pytest.approx(0.303)
    assert call.kwargs["reason"] == "malformed_position"
    ex.execute.assert_not_called()  # never arms a malformed position


@pytest.mark.asyncio
async def test_malformed_off_and_dry_run_never_write() -> None:
    """off/dry_run observe only for malformed positions."""
    for m in ("off", "dry_run"):
        r, ex, _, close_cb, _ = _make_remediator(mode=m, grace_sec=0)
        counts = await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        assert counts["skipped"] == 1
        assert counts["flattened"] == 0
        ex.execute.assert_not_called()
        close_cb.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_short_positive_amt_flattens() -> None:
    """AC2: SHORT with positive amt is also malformed and flattened."""
    r, ex, _, close_cb, clock = _make_remediator(mode="arm_or_flatten", grace_sec=0)
    div = _malformed_div(symbol="BTCUSDT", side="SHORT", qty=0.4, raw_amt=0.4)
    counts = await r.remediate([div], _binance_positions("BTCUSDT", "SHORT"))
    assert counts["flattened"] == 1
    call = close_cb.await_args
    assert call.kwargs["reason"] == "malformed_position"
    ex.execute.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_alert_latch_resets_after_clean_pass() -> None:
    """AC3: once resolved, a re-occurrence alerts CRITICAL again."""
    from tradeengine import naked_position_remediator as npr

    r, _, _, _, _ = _make_remediator(mode="arm_only")
    with patch.object(npr.logger, "critical") as mock_crit:
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        # Clean pass clears state.
        await r.remediate([], None)
        # Re-occurs — alerts again.
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    assert mock_crit.call_count == 2


# ---------------------------------------------------------------------------
# #566 — malformed-position stuck-duration gauge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_stuck_gauge_tracks_elapsed_age_in_arm_only() -> None:
    """arm_only never resolves a malformed position — the stuck gauge must
    reflect the growing age each cycle instead of staying at a fixed value."""
    from tradeengine import naked_position_remediator as npr

    r, _, _, _, clock = _make_remediator(mode="arm_only")
    with patch.object(npr.malformed_position_stuck_seconds, "labels") as mock_labels:
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        mock_labels.assert_any_call(symbol="LTCUSDT", side="LONG")
        first_set = mock_labels.return_value.set.call_args.args[0]
        assert first_set == pytest.approx(0.0)

        clock.advance(120)
        await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        second_set = mock_labels.return_value.set.call_args.args[0]
        assert second_set == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_malformed_stuck_gauge_zeroed_on_flatten() -> None:
    """arm_or_flatten flattening a malformed position must zero its stuck
    gauge rather than leaving the last observed age dangling."""
    from tradeengine import naked_position_remediator as npr

    r, _, _, _, clock = _make_remediator(mode="arm_or_flatten", grace_sec=60)
    await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    clock.advance(61)
    with patch.object(npr.malformed_position_stuck_seconds, "labels") as mock_labels:
        counts = await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
        assert counts["flattened"] == 1
        mock_labels.assert_any_call(symbol="LTCUSDT", side="LONG")
        assert mock_labels.return_value.set.call_args.args[0] == 0


@pytest.mark.asyncio
async def test_malformed_stuck_gauge_zeroed_on_clean_pass() -> None:
    """A clean pass (position resolved externally) must zero any stuck
    gauge left over from a prior malformed episode."""
    from tradeengine import naked_position_remediator as npr

    r, _, _, _, _ = _make_remediator(mode="arm_only")
    await r.remediate([_malformed_div()], _binance_positions("LTCUSDT"))
    with patch.object(npr.malformed_position_stuck_seconds, "labels") as mock_labels:
        await r.remediate([], None)
        mock_labels.assert_any_call(symbol="LTCUSDT", side="LONG")
        assert mock_labels.return_value.set.call_args.args[0] == 0


# ---------------------------------------------------------------------------
# #560 — bounded backoff + escalation after repeated re-arm failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_backoff_skips_after_max_consecutive_failures() -> None:
    """Per #560: after max_consecutive_arm_failures failed re-arm attempts on
    the same (symbol, side), the remediator must stop calling exchange.execute
    every cycle and instead skip until the cooldown expires — this is the
    fix for the observed infinite -4130 retry-every-cycle loop.
    """
    r, ex, _, _, clock = _make_remediator(
        mode="arm_only",
        exchange_execute_raises=True,
        max_consecutive_arm_failures=2,
        arm_backoff_cooldown_sec=120,
    )
    div = _unhedged_div()
    positions = _binance_positions()

    # Cycle 1: failure #1 — still under threshold, exchange is called.
    counts1 = await r.remediate([div], positions)
    assert counts1["failed"] == 1
    calls_after_1 = ex.execute.call_count
    assert calls_after_1 > 0

    # Cycle 2: failure #2 — hits threshold, backoff engages.
    clock.advance(1)
    counts2 = await r.remediate([div], positions)
    assert counts2["failed"] == 1
    calls_after_2 = ex.execute.call_count
    assert calls_after_2 > calls_after_1

    # Cycle 3: still within cooldown window — must be SKIPPED, not retried.
    clock.advance(1)
    counts3 = await r.remediate([div], positions)
    assert counts3["skipped"] == 1
    assert counts3["failed"] == 0
    assert ex.execute.call_count == calls_after_2  # no new calls made

    # Cycle 4: cooldown expired — attempts resume.
    clock.advance(121)
    counts4 = await r.remediate([div], positions)
    assert counts4["failed"] == 1
    assert ex.execute.call_count > calls_after_2


@pytest.mark.asyncio
async def test_arm_exhausted_alert_fires_once_per_backoff_episode() -> None:
    """The CRITICAL alert + naked_position_arm_exhausted_total counter fire
    exactly once when the threshold is crossed, not on every skipped cycle.
    """
    from tradeengine import naked_position_remediator as npr

    baseline = npr.naked_position_arm_exhausted_total.labels(
        symbol="BTCUSDT", side="LONG"
    )._value.get()

    r, _, _, _, clock = _make_remediator(
        mode="arm_only",
        exchange_execute_raises=True,
        max_consecutive_arm_failures=2,
        arm_backoff_cooldown_sec=120,
    )
    div = _unhedged_div()
    positions = _binance_positions()

    with patch.object(npr.logger, "critical") as mock_crit:
        await r.remediate([div], positions)  # failure 1
        clock.advance(1)
        await r.remediate([div], positions)  # failure 2 -> escalates
        clock.advance(1)
        await r.remediate([div], positions)  # skipped (cooldown)
        clock.advance(1)
        await r.remediate([div], positions)  # still skipped (cooldown)

    assert mock_crit.call_count == 1
    after = npr.naked_position_arm_exhausted_total.labels(
        symbol="BTCUSDT", side="LONG"
    )._value.get()
    assert after == baseline + 1


@pytest.mark.asyncio
async def test_arm_backoff_resets_on_successful_rearm() -> None:
    """A successful re-arm clears the consecutive-failure count so a later,
    unrelated failure streak starts counting from zero again.
    """
    r, ex, _, _, clock = _make_remediator(
        mode="arm_only",
        max_consecutive_arm_failures=2,
        arm_backoff_cooldown_sec=120,
    )
    div = _unhedged_div()
    positions = _binance_positions()

    # First make it fail once via a raising exchange.
    ex.execute = AsyncMock(side_effect=RuntimeError("boom"))
    counts1 = await r.remediate([div], positions)
    assert counts1["failed"] == 1

    # Then let it succeed — failure streak must reset.
    clock.advance(1)
    ex.execute = AsyncMock(return_value={"status": "FILLED"})
    counts2 = await r.remediate([div], positions)
    assert counts2["armed"] == 1
    assert r._consecutive_arm_failures.get(("BTCUSDT", "LONG")) is None

    # Fail again — should take a fresh 2 failures to trip backoff, not 1.
    clock.advance(1)
    ex.execute = AsyncMock(side_effect=RuntimeError("boom"))
    counts3 = await r.remediate([div], positions)
    assert counts3["failed"] == 1  # not skipped — streak reset


@pytest.mark.asyncio
async def test_arm_backoff_state_cleared_on_clean_pass() -> None:
    """A clean pass (no divergences) clears all #560 backoff bookkeeping."""
    r, _, _, _, clock = _make_remediator(
        mode="arm_only",
        exchange_execute_raises=True,
        max_consecutive_arm_failures=2,
        arm_backoff_cooldown_sec=120,
    )
    div = _unhedged_div()
    positions = _binance_positions()

    await r.remediate([div], positions)
    clock.advance(1)
    await r.remediate([div], positions)  # trips backoff
    key = ("BTCUSDT", "LONG")
    assert key in r._arm_backoff_until

    # Clean pass.
    await r.remediate([], None)
    assert key not in r._arm_backoff_until
    assert key not in r._consecutive_arm_failures
    assert key not in r._arm_exhausted_alerted
