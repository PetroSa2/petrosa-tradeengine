"""Per #415: verify the OTel SDK metric instruments are registered alongside
the existing prometheus_client instruments in tradeengine/metrics.py (dual-export).
Per #497: verify OCO orphan metrics are also dual-exported (OTel + prometheus_client).
"""

from opentelemetry.metrics import Counter, Histogram, UpDownCounter
from prometheus_client import (
    Counter as PromCounter,
    Gauge as PromGauge,
    Histogram as PromHistogram,
)

from tradeengine import metrics

OTEL_COUNTERS = [
    "otel_orders_executed_by_type",
    "otel_order_failures",
    "otel_positions_opened",
    "otel_positions_closed",
    "otel_risk_rejections",
    "otel_risk_checks",
]
OTEL_HISTOGRAMS = [
    "otel_order_execution_latency_seconds",
    "otel_position_pnl_usd",
]
OTEL_GAUGES = [
    "otel_total_realized_pnl_usd",
    "otel_total_unrealized_pnl_usd",
    "otel_total_daily_pnl_usd",
]


class TestOTelInstrumentsRegistered:
    def test_meter_created(self):
        assert metrics.meter is not None

    def test_otel_counters_registered(self):
        for name in OTEL_COUNTERS:
            assert isinstance(getattr(metrics, name), Counter), name

    def test_otel_histograms_registered(self):
        for name in OTEL_HISTOGRAMS:
            assert isinstance(getattr(metrics, name), Histogram), name

    def test_otel_gauges_registered(self):
        for name in OTEL_GAUGES:
            assert isinstance(getattr(metrics, name), UpDownCounter), name


class TestPrometheusDualExportPreserved:
    """AC: existing prometheus_client instruments remain untouched (dual-export)."""

    def test_prometheus_counter_preserved(self):
        assert isinstance(metrics.orders_executed_by_type, PromCounter)

    def test_prometheus_histogram_preserved(self):
        assert isinstance(metrics.order_execution_latency_seconds, PromHistogram)

    def test_prometheus_gauge_preserved(self):
        assert isinstance(metrics.total_realized_pnl_usd, PromGauge)


class TestOCOOrphanDualExport:
    """#497 — OCO orphan metrics must be dual-exported (prometheus_client + OTel OTLP).

    AC1: petrosa_tradeengine_oco_orphan_leg_total flows via OTLP.
    AC2: petrosa_tradeengine_oco_orphan_count flows via OTLP.
    """

    def test_otel_oco_orphan_leg_is_counter(self):
        """otel_oco_orphan_leg must be an OTel Counter registered on the shared meter."""
        assert isinstance(metrics.otel_oco_orphan_leg, Counter)

    def test_otel_oco_orphan_count_is_up_down_counter(self):
        """otel_oco_orphan_count must be an OTel UpDownCounter (supports decrement)."""
        assert isinstance(metrics.otel_oco_orphan_count, UpDownCounter)

    def test_prometheus_oco_orphan_leg_preserved(self):
        """prometheus_client oco_orphan_leg_total must still exist (dual-export)."""
        assert isinstance(metrics.oco_orphan_leg_total, PromCounter)

    def test_prometheus_oco_orphan_count_exists(self):
        """prometheus_client oco_orphan_count Gauge must now exist (AC2 of #497)."""
        assert isinstance(metrics.oco_orphan_count, PromGauge)
