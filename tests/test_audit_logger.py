"""Tests for shared.audit.AuditLogger.

Covers #548: null-safe handling of order/result payloads with unfilled
(fill_price=None, pnl=None, timestamp=None, total_value=0) MARKET orders,
and the tradeengine_audit_write_failed_total observability counter (AC3).
"""

import logging
from unittest.mock import patch

from shared.audit import AuditLogger, audit_write_failed_total


def _counter_value(method: str) -> float:
    return audit_write_failed_total.labels(method=method)._value.get()


def test_log_order_with_null_result_fields_does_not_raise(caplog):
    """#548 AC2: AuditLogger.log_order with a result dict containing null
    fill_price/total_value/pnl/timestamp must not raise and must write a
    record.
    """
    caplog.set_level(logging.INFO)
    logger = AuditLogger()

    order_data = {
        "order": {
            "symbol": "XRPUSDT",
            "type": "market",
            "side": "buy",
            "amount": 10.0,
            "stop_loss": 1.1012,
            "take_profit": 1.1234,
        },
        "result": {
            "order_id": "3481918562",
            "status": "NEW",
            "side": "BUY",
            "type": "MARKET",
            "amount": 10.0,
            "fill_price": None,
            "total_value": 0,
            "fees": 0.0,
            "fee_asset": None,
            "pnl": None,
            "timestamp": None,
        },
    }

    logger.log_order(order_data)

    assert "Order logged:" in caplog.text
    assert "float() argument" not in caplog.text
    assert "not 'NoneType'" not in caplog.text


def test_log_error_with_order_result_context_does_not_raise(caplog):
    """The exact evidence shape from #548: log_error({'error': ...},
    context={'order': ..., 'result': ...}) must not itself raise, regardless
    of what null fields the upstream caller forwards.
    """
    logger = AuditLogger()

    logger.log_error(
        {"error": "float() argument must be a string or a real number, not 'NoneType'"},
        context={
            "order": {"symbol": "XRPUSDT", "side": "buy", "amount": 10.0},
            "result": {"fill_price": None, "pnl": None, "timestamp": None},
        },
    )

    assert "Error logged:" in caplog.text


def test_audit_write_failed_total_increments_on_internal_exception():
    """#548 AC3: a genuine AuditLogger internal failure (not an upstream
    computation error merely being *reported* via log_error) must increment
    `tradeengine_audit_write_failed_total{method="log_order"}` rather than
    being silently swallowed.
    """
    logger = AuditLogger()
    before = _counter_value("log_order")

    with patch.object(logger.logger, "info", side_effect=RuntimeError("boom")):
        logger.log_order({"order": {"symbol": "BTCUSDT"}})

    after = _counter_value("log_order")
    assert after == before + 1.0


def test_audit_write_failed_total_increments_for_log_error():
    """log_error's primary emission also goes through self.logger.error, so
    the first call must fail and the except-branch's own fallback call must
    succeed (it uses the same method) to observe the counter incrementing
    without an unhandled exception escaping the test.
    """
    logger = AuditLogger()
    before = _counter_value("log_error")

    with patch.object(logger.logger, "error", side_effect=[RuntimeError("boom"), None]):
        logger.log_error({"error": "x"}, context={"order": {}, "result": {}})

    after = _counter_value("log_error")
    assert after == before + 1.0


def test_log_order_disabled_is_noop():
    logger = AuditLogger()
    logger.enabled = False
    before = _counter_value("log_order")

    # Should return immediately without touching the counter or logger.
    logger.log_order({"order": {}, "result": {"fill_price": None}})

    assert _counter_value("log_order") == before
