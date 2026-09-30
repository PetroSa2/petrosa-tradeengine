import json
import logging

from prometheus_client import REGISTRY

from tradeengine.execution_observability import (
    ALLOWED_LABELS,
    TradeExecutionObservability,
    trade_order_duration_seconds,
    trade_orders_total,
)


def test_metrics_have_exact_bounded_labels() -> None:
    assert (
        set(trade_orders_total._labelnames)
        == ALLOWED_LABELS["petrosa_trade_orders_total"]
    )
    assert (
        set(trade_order_duration_seconds._labelnames)
        == ALLOWED_LABELS["petrosa_trade_order_duration_seconds"]
    )


def test_record_order_increments_counter_and_histogram() -> None:
    observability = TradeExecutionObservability(logging.getLogger("test"))
    before = trade_orders_total.labels("buy", "market", "accepted")._value.get()
    observability.record_order(
        side="BUY", order_type="MARKET", outcome="accepted", duration_seconds=0.25
    )
    assert (
        trade_orders_total.labels("buy", "market", "accepted")._value.get()
        == before + 1
    )


def test_summary_shape_has_no_unbounded_fields() -> None:
    observability = TradeExecutionObservability(logging.getLogger("test"))
    observability.record_order(
        side="buy", order_type="limit", outcome="accepted", duration_seconds=0.1
    )
    summary = observability.summary()
    encoded = json.dumps(summary)
    assert summary["event"] == "SUMMARY"
    assert summary["window_seconds"] == 300
    assert summary["service"] == "petrosa-tradeengine"
    assert "order_id" not in encoded
    assert "symbol" not in encoded


def test_summary_emits_once_when_window_is_due() -> None:
    messages: list[str] = []
    logger = logging.getLogger("summary-test")
    logger.addHandler(logging.Handler())
    logger.info = messages.append  # type: ignore[method-assign]
    now = [0.0]
    observability = TradeExecutionObservability(logger, clock=lambda: now[0])
    assert not observability.emit_summary()
    now[0] = 300.0
    assert observability.emit_summary()
    payload = json.loads(messages[0])
    assert payload["event"] == "SUMMARY"


def test_registered_metric_names_are_expected() -> None:
    names = {metric.name for metric in REGISTRY.collect()}
    assert "petrosa_trade_orders" in names
    assert "petrosa_trade_order_duration_seconds" in names
