from __future__ import annotations

from scripts.forensics.ledger_forensics import (
    classify_fill,
    commission_report,
    count_rejections,
    entry_legs,
    group_persist_events,
)


def test_entry_walk_and_candle_classification():
    legs, bounded = entry_legs(
        [{"time": "2026-09-24T04:00:00Z", "qty": "264.676", "price": "57.88"}],
        264.676,
        lookback_days=7,
    )
    assert not bounded
    assert legs[0]["qty"] == 264.676
    assert classify_fill(legs, {"2026-09-24T04:00": {"low": 57, "high": 58}}) == "GENUINE"
    assert classify_fill(legs, {"2026-09-24T04:00": {"low": 58, "high": 59}}) == "TESTNET_ARTIFACT"


def test_trace_rejections_and_commissions_are_grouped():
    trace = group_persist_events(
        [{"created_at": "2026-09-26T20:00:00Z", "position_id": "missing", "write_mode": "incremental"}],
        [{"id": "present"}],
    )
    assert trace["days"]["2026-09-26"]["without_position"] == 1
    assert count_rejections("error -4164; error -1013") == {"-4164": 1, "-1013": 1}
    result = commission_report(
        [{"symbol": "LTCUSDT", "incomeType": "COMMISSION", "income": "-10"}],
        [{"symbol": "LTCUSDT", "commission": "8"}],
    )
    assert result["LTCUSDT"]["difference"] == 2
