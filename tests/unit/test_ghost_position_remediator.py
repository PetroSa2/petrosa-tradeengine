"""Unit tests for GhostPositionRemediator (#592)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradeengine.ghost_position_remediator import GhostPositionRemediator


def _ghost_div(symbol: str, side: str, qty: float = 0.303) -> dict:
    return {
        "category": "ghost",
        "symbol": symbol,
        "side": side,
        "binance_qty": 0.0,
        "local_qty": qty,
        "detail": "Position in local tracker but absent from Binance",
    }


def _pm_with_position(symbol: str, side: str, qty: float = 0.303) -> MagicMock:
    pm = MagicMock()
    pm.positions = {(symbol, side): {"quantity": qty, "avg_price": 47.99}}
    return pm


# ---------------------------------------------------------------------------
# mode coercion
# ---------------------------------------------------------------------------


def test_coerce_mode_defaults_to_void_on_garbage():
    r = GhostPositionRemediator(position_manager=MagicMock(), mode="bogus")  # type: ignore[arg-type]
    assert r.mode == "void"


def test_coerce_mode_accepts_known_values():
    for m in ("off", "dry_run", "void"):
        r = GhostPositionRemediator(position_manager=MagicMock(), mode=m)
        assert r.mode == m


# ---------------------------------------------------------------------------
# mode == "void" — the default, exchange-safe write path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_void_mode_removes_stale_entry_and_audits():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")
    divergences = [_ghost_div("LTCUSDT", "LONG")]

    with patch("tradeengine.ghost_position_remediator.audit_logger") as mock_audit:
        voided = await remediator.remediate(divergences)

    assert ("LTCUSDT", "LONG") not in pm.positions
    assert len(voided) == 1
    assert divergences[0]["resolution"] == "voided"
    assert "resolution_reason" in divergences[0]
    mock_audit.log_position.assert_called_once()
    _, kwargs = mock_audit.log_position.call_args
    assert kwargs["status"] == "voided_ghost"


@pytest.mark.asyncio
async def test_void_mode_increments_metric():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")

    with (
        patch("tradeengine.ghost_position_remediator.audit_logger"),
        patch(
            "tradeengine.ghost_position_remediator.ghost_positions_voided_total"
        ) as mock_counter,
    ):
        await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])

    mock_counter.labels.assert_called_once_with(symbol="LTCUSDT", side="LONG")
    mock_counter.labels.return_value.inc.assert_called_once()


@pytest.mark.asyncio
async def test_void_mode_is_idempotent_no_second_audit_entry():
    """#592 AC: running reconciliation twice must not produce a second
    audit entry for the same ghost."""
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")

    with patch("tradeengine.ghost_position_remediator.audit_logger") as mock_audit:
        first = await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])
        second = await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])

    assert len(first) == 1
    assert len(second) == 0
    mock_audit.log_position.assert_called_once()


@pytest.mark.asyncio
async def test_void_mode_second_call_marks_already_voided():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")

    with patch("tradeengine.ghost_position_remediator.audit_logger"):
        await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])
        div2 = _ghost_div("LTCUSDT", "LONG")
        await remediator.remediate([div2])

    assert div2["resolution"] == "already_voided"


@pytest.mark.asyncio
async def test_void_mode_only_touches_matching_key():
    pm = MagicMock()
    pm.positions = {
        ("LTCUSDT", "LONG"): {"quantity": 0.303},
        ("BTCUSDT", "LONG"): {"quantity": 1.0},
    }
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")

    with patch("tradeengine.ghost_position_remediator.audit_logger"):
        await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])

    assert ("LTCUSDT", "LONG") not in pm.positions
    assert ("BTCUSDT", "LONG") in pm.positions


@pytest.mark.asyncio
async def test_never_re_materialises_on_exchange():
    """#592 review finding: a ghost must be voided, never re-opened by
    placing a real order. The remediator has no exchange handle at all."""
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")
    assert not hasattr(remediator, "_exchange")
    with patch("tradeengine.ghost_position_remediator.audit_logger"):
        await remediator.remediate([_ghost_div("LTCUSDT", "LONG")])


# ---------------------------------------------------------------------------
# mode == "dry_run"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_mode_does_not_mutate_state():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="dry_run")
    divergences = [_ghost_div("LTCUSDT", "LONG")]

    with patch("tradeengine.ghost_position_remediator.audit_logger") as mock_audit:
        voided = await remediator.remediate(divergences)

    assert ("LTCUSDT", "LONG") in pm.positions
    assert voided == []
    assert divergences[0]["resolution"] == "would_void"
    mock_audit.log_position.assert_not_called()


# ---------------------------------------------------------------------------
# mode == "off"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_mode_does_not_mutate_state_or_audit():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="off")
    divergences = [_ghost_div("LTCUSDT", "LONG")]

    with patch("tradeengine.ghost_position_remediator.audit_logger") as mock_audit:
        voided = await remediator.remediate(divergences)

    assert ("LTCUSDT", "LONG") in pm.positions
    assert voided == []
    assert divergences[0]["resolution"] == "skipped_mode_off"
    mock_audit.log_position.assert_not_called()


@pytest.mark.asyncio
async def test_empty_divergence_list_is_noop():
    pm = _pm_with_position("LTCUSDT", "LONG")
    remediator = GhostPositionRemediator(position_manager=pm, mode="void")
    with patch("tradeengine.ghost_position_remediator.audit_logger") as mock_audit:
        voided = await remediator.remediate([])
    assert voided == []
    mock_audit.log_position.assert_not_called()
    assert ("LTCUSDT", "LONG") in pm.positions
