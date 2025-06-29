from pydantic import BaseModel
from typing import Literal
from datetime import datetime


class Signal(BaseModel):
    strategy_id: str
    symbol: str
    action: Literal["buy", "sell", "hold"]
    price: float
    confidence: float
    timestamp: datetime
    meta: dict  # e.g. indicators, volatility, rationale
