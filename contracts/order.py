from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    """Order side options"""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Order type options"""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    CONDITIONAL_LIMIT = "conditional_limit"
    CONDITIONAL_STOP = "conditional_stop"


class OrderStatus(str, Enum):
    """Order status options"""

    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TradeOrder(BaseModel):
    """Trade order model with advanced features"""

    # Core order information
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    type: str = Field(..., description="Order type")
    side: str = Field(..., description="Order side (buy/sell)")
    amount: float = Field(..., description="Order amount")

    # Price information
    target_price: float | None = Field(None, description="Target execution price")
    stop_loss: float | None = Field(None, description="Stop loss price")
    take_profit: float | None = Field(None, description="Take profit price")

    # Conditional order parameters
    conditional_price: float | None = Field(
        None, description="Price level for conditional execution"
    )
    conditional_direction: str | None = Field(
        None, description="Direction for conditional execution"
    )
    conditional_timeout: int | None = Field(
        None, description="Timeout in seconds for conditional orders"
    )

    # Advanced order features
    iceberg_quantity: float | None = Field(
        None, description="Iceberg quantity for iceberg orders"
    )
    client_order_id: str | None = Field(None, description="Client-provided order ID")

    # Order metadata
    order_id: str | None = Field(None, description="Exchange order ID")
    status: OrderStatus = Field(OrderStatus.PENDING, description="Order status")
    filled_amount: float = Field(0.0, description="Amount filled so far")
    average_price: float | None = Field(None, description="Average fill price")

    # Simulation flag
    simulate: bool = Field(True, description="Whether to simulate the order")

    # Reduce-only flag (exempt from MIN_NOTIONAL validation)
    reduce_only: bool = Field(
        False, description="Reduce-only order (exempt from MIN_NOTIONAL)"
    )

    # Time in force (for limit/stop orders)
    time_in_force: str | None = Field(
        None, description="Time in force policy (GTC, IOC, etc.)"
    )

    # Position sizing (for risk management)
    position_size_pct: float | None = Field(
        None, description="Position size as a percent of portfolio"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Order creation timestamp"
    )
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    # Metadata
    meta: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
