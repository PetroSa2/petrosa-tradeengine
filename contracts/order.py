from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone

    UTC = timezone.utc  # noqa: UP017
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self):
            return str(self.value)


class OrderSide(StrEnum):
    """Order side options"""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Order type options"""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    CONDITIONAL_LIMIT = "conditional_limit"
    CONDITIONAL_STOP = "conditional_stop"


class OrderStatus(StrEnum):
    """Order status options"""

    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TradeOrder(BaseModel):
    """Trade order model with advanced features"""

    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    type: str = Field(..., description="Order type")
    side: str = Field(..., description="Order side (buy/sell)")
    amount: float = Field(..., description="Order amount")
    target_price: float | None = Field(None, description="Target execution price")
    stop_loss: float | None = Field(None, description="Stop loss price")
    take_profit: float | None = Field(None, description="Take profit price")
    conditional_price: float | None = Field(
        None, description="Price level for conditional execution"
    )
    conditional_direction: str | None = Field(
        None, description="Direction for conditional execution"
    )
    conditional_timeout: int | None = Field(
        None, description="Timeout in seconds for conditional orders"
    )
    iceberg_quantity: float | None = Field(
        None, description="Iceberg quantity for iceberg orders"
    )
    client_order_id: str | None = Field(None, description="Client-provided order ID")
    order_id: str | None = Field(None, description="Exchange order ID")
    status: OrderStatus = Field(OrderStatus.PENDING, description="Order status")
    filled_amount: float = Field(0.0, description="Amount filled so far")
    average_price: float | None = Field(None, description="Average fill price")
    position_id: str | None = Field(None, description="Unique position ID for tracking")
    position_side: str | None = Field(
        None, description="Position side for hedge mode (LONG/SHORT)"
    )
    exchange: str = Field("binance", description="Exchange identifier")
    strategy_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Strategy parameters for tracking"
    )
    simulate: bool = Field(True, description="Whether to simulate the order")
    reduce_only: bool = Field(
        False, description="Reduce-only order (exempt from MIN_NOTIONAL)"
    )
    time_in_force: str | None = Field(
        None, description="Time in force policy (GTC, IOC, etc.)"
    )
    position_size_pct: float | None = Field(
        None, description="Position size as a percent of portfolio"
    )
    stop_loss_pct: float | None = Field(
        None, description="Stop loss as percentage of entry price"
    )
    take_profit_pct: float | None = Field(
        None, description="Take profit as percentage of entry price"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Order creation timestamp",
    )
    updated_at: datetime | None = Field(None, description="Last update timestamp")
    # P6.2 (#608): rejection persistence per FR41. All three fields are
    # additive + optional so deserializing pre-#608 TradeOrders (which
    # carry no rejection fields) still succeeds.
    rejection_reason: str | None = Field(
        None,
        description=(
            "Human-readable rejection reason. Free-form text or exchange "
            "error code+message — e.g., 'balance_below_minimum', "
            "'symbol_not_whitelisted', '4001: insufficient margin'."
        ),
    )
    rejected_at: datetime | None = Field(
        None, description="UTC timestamp when the rejection was emitted"
    )
    rejection_source: (
        Literal[
            "risk_check",
            "exchange",
            "stale_signal",
            "whitelist",
            "balance",
            "validation",
        ]
        | None
    ) = Field(
        None,
        description=(
            "Bounded enum identifying which subsystem rejected the order. "
            "Kept as a Literal rather than free-form so dashboards stay "
            "queryable; new sources require a contract bump."
        ),
    )
    meta: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }

    def mark_rejected(
        self,
        *,
        source: Literal[
            "risk_check",
            "exchange",
            "stale_signal",
            "whitelist",
            "balance",
            "validation",
        ],
        reason: str,
        rejected_at: datetime | None = None,
    ) -> "TradeOrder":
        """Mutate this TradeOrder into the rejected state in one call.

        Sets `status = REJECTED`, populates `rejection_reason`,
        `rejection_source`, and `rejected_at` (default: now in UTC),
        and updates `updated_at`. Returns self for fluent use at
        rejection call-sites so producers don't need to know which
        fields to keep in sync.
        """
        ts = rejected_at or datetime.now(UTC)
        self.status = OrderStatus.REJECTED
        self.rejection_reason = reason
        self.rejection_source = source
        self.rejected_at = ts
        self.updated_at = ts
        return self
