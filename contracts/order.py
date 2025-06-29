from pydantic import BaseModel
from typing import Optional, Literal


class TradeOrder(BaseModel):
    type: Literal["market", "limit", "stop"]
    side: Literal["buy", "sell"]
    amount: float
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    time_in_force: Optional[str] = None
    simulate: bool = False
