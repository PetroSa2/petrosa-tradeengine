"""Unit tests for NakedPositionRemediator (#445).

Covers all four modes (off, dry_run, arm_only, arm_or_flatten),
grace-window flatten escalation, idempotent re-arm, fallback SL/TP
derivation, and clean-pass first-seen reset.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    sl, tp = r._derive_protective_prices(
        "BTCUSDT", "LONG", _binance_positions(entry=100.0)
    )
    assert sl == pytest.approx(98.0)  # 100 * (1 - 2%)
    assert tp == pytest.approx(104.0)  # 100 * (1 + 4%)


def test_derive_uses_fallback_for_short_when_no_local_record() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only")
    sl, tp = r._derive_protective_prices(
        "BTCUSDT", "SHORT", _binance_positions("BTCUSDT", "SHORT", 100.0)
    )
    assert sl == pytest.approx(102.0)  # 100 * (1 + 2%)
    assert tp == pytest.approx(96.0)  # 100 * (1 - 4%)


def test_derive_returns_none_for_missing_entry_price() -> None:
    r, _, _, _, _ = _make_remediator(mode="arm_only")
    sl, tp = r._derive_protective_prices(
        "BTCUSDT", "LONG", {("BTCUSDT", "LONG"): {"entryPrice": 0}}
    )
    assert sl is None and tp is None


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
