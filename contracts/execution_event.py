"""Execution event contract published on the execution.events subject."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ExecutionEventType = Literal[
    "placed",
    "filled",
    "partial_fill",
    "rejected",
    "position_force_closed_no_stops",
    "position_closed",
]


class ExecutionEvent(BaseModel):
    """Common execution event envelope and additive close-row fields."""

    model_config = ConfigDict(extra="allow")

    decision_id: str = ""
    strategy_id: str
    client_order_id: str = ""
    order_id: str = ""
    event_type: ExecutionEventType
    timestamp: datetime
    reason: str = ""
    position_id: str | None = None
    entry_order_id: str | None = None
    closed_quantity: float | None = None
    remaining_quantity: float | None = None
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_order_id: str | None = None
    pnl_basis: Literal["fifo_attributed", "unknown"] | None = None
    pnl: float | None = None
    fee: float | None = None
