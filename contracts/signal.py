from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Signal(BaseModel):
    strategy_id: str
    symbol: str
    action: Literal["buy", "sell", "hold"]
    price: float
    confidence: float
    timestamp: datetime
    meta: dict  # e.g. indicators, volatility, rationale
