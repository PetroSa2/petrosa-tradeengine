"""tradeengine subsystem evaluator (P2.7, petrosa_k8s#697 AC5 / #417).

Adopts the shared `petrosa_otel.evaluators` framework (P2.1) so tradeengine
publishes a structured health verdict on ``evaluator.tradeengine.verdict``,
closing the last of the five "silent service" gaps that keep
FR17 / FR23 / FR32 at YELLOW. Read-only over existing Prometheus metrics —
this module never touches the order / risk / position logic.
"""

from tradeengine.evaluators.health_evaluator import (
    TradeEngineHealthEvaluator,
    build_tradeengine_health_evaluator,
)

__all__ = [
    "TradeEngineHealthEvaluator",
    "build_tradeengine_health_evaluator",
]
