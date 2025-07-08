from typing import Literal

from pydantic import BaseModel, Field


class TradeOrder(BaseModel):
    """Trade order model with support for all order types"""

    # Required fields
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    type: Literal[
        "market", "limit", "stop", "stop_limit", "take_profit", "take_profit_limit"
    ] = Field(..., description="Order type")
    side: Literal["buy", "sell"] = Field(..., description="Order side")
    amount: float = Field(..., description="Order quantity")

    # Optional price fields
    target_price: float | None = Field(None, description="Limit price for limit orders")
    stop_loss: float | None = Field(None, description="Stop loss price for stop orders")
    take_profit: float | None = Field(
        None, description="Take profit price for take profit orders"
    )

    # Order parameters
    time_in_force: str | None = Field(
        "GTC", description="Time in force (GTC, IOC, FOK)"
    )
    quote_quantity: float | None = Field(
        None, description="Quote quantity for market orders"
    )

    # Risk management
    simulate: bool = Field(True, description="Whether to simulate the order")

    # Metadata
    strategy_id: str | None = Field(
        None, description="Strategy that generated this order"
    )
    signal_id: str | None = Field(None, description="Original signal ID")
    meta: dict = Field(default_factory=dict, description="Additional metadata")

    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "BTCUSDT",
                "type": "limit",
                "side": "buy",
                "amount": 0.001,
                "target_price": 45000.0,
                "time_in_force": "GTC",
                "simulate": True,
                "strategy_id": "momentum_v1",
                "meta": {"confidence": 0.85},
            }
        }
