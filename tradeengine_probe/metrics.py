"""Prometheus metrics owned by the isolated probe process."""

from prometheus_client import Counter, Gauge, Histogram

CHECKS = (
    "time_sync",
    "signed_read",
    "account",
    "order_test_market",
    "order_test_limit",
    "order_test_negative_control",
    "filters_changed",
)
RESULTS = (
    "success",
    "auth_error",
    "clock_error",
    "exchange_reject",
    "rate_limited",
    "timeout",
    "transport_error",
    "blocked",
    "unexpected",
    "skipped",
)

runs = Counter(
    "tradeengine_synthetic_probe_runs_total",
    "Probe check outcomes",
    ("check", "result"),
)
duration = Histogram(
    "tradeengine_synthetic_probe_duration_seconds", "Probe check duration", ("check",)
)
last_success = Gauge(
    "tradeengine_synthetic_probe_last_success_timestamp_seconds",
    "Last successful check time",
    ("check",),
)
last_run = Gauge(
    "tradeengine_synthetic_probe_last_run_timestamp_seconds", "Last cycle time"
)
interval = Gauge(
    "tradeengine_synthetic_probe_interval_seconds", "Configured cycle interval"
)
clock_skew = Gauge(
    "tradeengine_synthetic_probe_clock_skew_seconds", "Exchange clock skew"
)
recv_window = Gauge(
    "tradeengine_synthetic_probe_recv_window_seconds", "Configured receive window"
)
last_error = Gauge(
    "tradeengine_synthetic_probe_last_error_code",
    "Last exchange error code",
    ("check",),
)
filters_changed = Gauge(
    "tradeengine_synthetic_probe_filters_changed",
    "Whether exchange filters changed",
    ("symbol",),
)
used_weight = Gauge("tradeengine_synthetic_probe_used_weight_1m", "Binance used weight")
order_count_10s = Gauge(
    "tradeengine_synthetic_probe_order_count_10s", "Binance order count"
)
info = Gauge(
    "tradeengine_synthetic_probe_info",
    "Probe identity",
    ("mode", "testnet", "endpoint_host"),
)


def initialise_series() -> None:
    for check in CHECKS:
        for result in RESULTS:
            runs.labels(check=check, result=result)
        duration.labels(check=check)
        last_success.labels(check=check)
        last_error.labels(check=check)
