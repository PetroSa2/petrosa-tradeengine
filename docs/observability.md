# TradeEngine observability

Trade execution telemetry uses the fixed low-cardinality catalog from
`petrosa_k8s#1232`:

- `petrosa_trade_orders_total{side,order_type,outcome}` is the canonical
  execution counter.
- `petrosa_trade_order_duration_seconds{operation,outcome}` is the canonical
  execution-duration histogram.

The older `petrosa_tradeengine_orders_total{route_status,symbol,exchange}`
series remains for alert compatibility with `petrosa_k8s#1022` and
tradeengine#569. It is not used for the new summary catalog and is not
extended with unbounded labels.

Every 300 seconds, and during shutdown, TradeEngine emits one structured
`SUMMARY` INFO record containing only bounded outcome counts, protective-leg
action count, and latency p50/p95. Per-order detail and expected skips are
DEBUG; handled fallbacks remain WARN.
