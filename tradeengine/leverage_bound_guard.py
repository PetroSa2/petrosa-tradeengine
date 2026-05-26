"""
Leverage Bound Guard — FR64 / P6.4

Pre-placement enforcement of operator-configured leverage bounds.

Checks:
- AC2: Per-placement check: the strategy's configured leverage must not exceed
       max_leverage_bound for the relevant scope (strategy → symbol → global).
- AC3: Portfolio-aggregate: even if AC2 passes, the sum of configured leverage
       across all currently-open positions must not exceed portfolio_leverage_cap
       after the order is placed.
- AC5: Repeated breach attempts (consecutive rejections on the same scope)
       increment leverage_bound_rejections_total; when the count hits
       leverage_breach_alert_threshold the operator alert metric fires.
"""

import logging
from typing import Any

from prometheus_client import Counter, Gauge

from contracts.order import TradeOrder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics (AC5)
# ---------------------------------------------------------------------------

leverage_bound_rejections_total = Counter(
    "tradeengine_leverage_bound_rejections_total",
    "Total orders rejected because configured leverage exceeded the operator bound",
    ["scope", "symbol", "strategy_id"],
)

leverage_bound_breach_alert = Gauge(
    "tradeengine_leverage_bound_breach_alert",
    "1 when the consecutive-breach threshold has been reached for any scope",
    ["scope"],
)


class LeverageBoundGuard:
    """
    Stateful pre-trade guard that enforces operator-configured leverage limits.

    Inject once into the Dispatcher and call `check(order, config, open_positions)`
    before executing each order.
    """

    def __init__(self) -> None:
        # Tracks consecutive breach counts per (strategy_id, symbol) scope.
        self._consecutive_breaches: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        order: TradeOrder,
        resolved_config: dict[str, Any],
        open_position_leverages: list[int],
    ) -> tuple[bool, str]:
        """
        Run AC2 + AC3 leverage bound checks.

        Args:
            order: The TradeOrder about to be placed.
            resolved_config: The fully-resolved TradingConfig parameters for this
                             order's scope (strategy + symbol). Must include:
                             - "leverage": int  (configured trade leverage)
                             - "max_leverage_bound": int  (per-scope cap)
                             - "portfolio_leverage_cap": int  (aggregate cap, 0=disabled)
                             - "leverage_breach_alert_threshold": int
            open_position_leverages: List of leverage values for every currently-open
                                     position tracked by the dispatcher (used for AC3).

        Returns:
            (True, "") if both checks pass.
            (False, rejection_reason_str) if either check fails.
        """
        strategy_configured_leverage: int = int(resolved_config.get("leverage", 10))
        max_leverage_bound: int = int(resolved_config.get("max_leverage_bound", 125))
        portfolio_leverage_cap: int = int(
            resolved_config.get("portfolio_leverage_cap", 0)
        )
        alert_threshold: int = int(
            resolved_config.get("leverage_breach_alert_threshold", 3)
        )

        scope_key = (
            f"{order.strategy_metadata.get('strategy_id') or 'unknown'}:{order.symbol}"
        )

        # -- AC2: per-strategy bound ------------------------------------------
        if strategy_configured_leverage > max_leverage_bound:
            reason = (
                f"Configured leverage {strategy_configured_leverage}x exceeds "
                f"operator bound {max_leverage_bound}x for scope {scope_key}"
            )
            self._record_breach(
                scope_key,
                alert_threshold,
                order.symbol,
                strategy_id=str(order.strategy_metadata.get("strategy_id", "")),
            )
            logger.warning(
                f"LEVERAGE_BOUND_REJECT (AC2): {order.symbol} "
                f"strategy_leverage={strategy_configured_leverage}x "
                f"bound={max_leverage_bound}x scope={scope_key}"
            )
            return False, reason

        # -- AC3: portfolio-aggregate cap -------------------------------------
        if portfolio_leverage_cap > 0:
            aggregate = sum(open_position_leverages) + strategy_configured_leverage
            if aggregate > portfolio_leverage_cap:
                reason = (
                    f"Portfolio aggregate leverage {aggregate}x would exceed cap "
                    f"{portfolio_leverage_cap}x (existing={sum(open_position_leverages)}x + "
                    f"new={strategy_configured_leverage}x)"
                )
                self._record_breach(
                    f"portfolio:{order.symbol}",
                    alert_threshold,
                    order.symbol,
                    strategy_id=str(order.strategy_metadata.get("strategy_id", "")),
                )
                logger.warning(
                    f"LEVERAGE_BOUND_REJECT (AC3): {order.symbol} "
                    f"aggregate={aggregate}x cap={portfolio_leverage_cap}x"
                )
                return False, reason

        # Both checks passed — reset breach counters for both scope keys.
        self._reset_breach(scope_key)
        self._reset_breach(f"portfolio:{order.symbol}")
        return True, ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_breach(
        self,
        scope_key: str,
        alert_threshold: int,
        symbol: str,
        strategy_id: str,
    ) -> None:
        """Increment consecutive breach counter; fire alert metric when threshold hit."""
        self._consecutive_breaches[scope_key] = (
            self._consecutive_breaches.get(scope_key, 0) + 1
        )
        count = self._consecutive_breaches[scope_key]

        leverage_bound_rejections_total.labels(
            scope=scope_key,
            symbol=symbol,
            strategy_id=strategy_id,
        ).inc()

        if count >= alert_threshold:
            logger.error(
                f"LEVERAGE_BOUND_ALERT: scope={scope_key} has breached the leverage "
                f"bound {count} consecutive times (threshold={alert_threshold}). "
                f"Possible CIO misconfiguration or compromised signal source."
            )
            leverage_bound_breach_alert.labels(scope=scope_key).set(1)

    def _reset_breach(self, scope_key: str) -> None:
        """Reset counter and clear alert gauge when an order passes."""
        if scope_key in self._consecutive_breaches:
            del self._consecutive_breaches[scope_key]
            leverage_bound_breach_alert.labels(scope=scope_key).set(0)
