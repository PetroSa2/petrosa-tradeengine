from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        def __str__(self):
            return str(self.value)

class SignalType(StrEnum):
    """Signal types for trading actions"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"

class SignalStrength(StrEnum):
    """Signal strength levels"""
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"
    EXTREME = "extreme"

class TimeFrame(StrEnum):
    """Trading timeframes for signal analysis"""
    TICK = "tick"
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    HOUR_2 = "2h"
    HOUR_4 = "4h"
    HOUR_6 = "6h"
    HOUR_8 = "8h"
    HOUR_12 = "12h"
    DAY_1 = "1d"
    DAY_3 = "3d"
    WEEK_1 = "1w"
    MONTH_1 = "1M"

class OrderType(StrEnum):
    """Supported order types"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    CONDITIONAL_LIMIT = "conditional_limit"
    CONDITIONAL_STOP = "conditional_stop"

class TimeInForce(StrEnum):
    """Order time in force options"""
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"

class StrategyMode(StrEnum):
    """Strategy processing modes"""
    DETERMINISTIC = "deterministic"
    ML_LIGHT = "ml_light"
    LLM_REASONING = "llm_reasoning"

class Signal(BaseModel):
    """Enhanced trading signal with advanced features"""
    id: str | None = Field(None, description="Unique identifier for this signal")
    strategy_id: str = Field(..., description="Unique identifier for the strategy")
    signal_id: str | None = Field(None, description="Unique identifier for this signal")
    strategy_mode: StrategyMode = Field(StrategyMode.DETERMINISTIC, description="Processing mode for this signal")
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    signal_type: SignalType | None = Field(None, description="Signal type (buy/sell/hold/close) - deprecated, use action")
    action: Literal["buy", "sell", "hold", "close"] = Field(..., description="Trading action")
    confidence: float = Field(..., ge=0, le=1, description="Signal confidence (0-1)")
    strength: SignalStrength = Field(SignalStrength.MEDIUM, description="Signal strength level")
    price: float = Field(..., description="Signal price")
    quantity: float = Field(..., description="Signal quantity")
    current_price: float = Field(..., description="Current market price")
    target_price: float | None = Field(None, description="Target execution price")
    source: str = Field(..., description="Signal source")
    strategy: str = Field(..., description="Strategy name")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timeframe: str = Field("1h", description="Timeframe used for signal analysis")
    order_type: OrderType = Field(OrderType.MARKET, description="Order type to execute")
    time_in_force: TimeInForce = Field(TimeInForce.GTC, description="Order time in force")
    position_size_pct: float | None = Field(None, ge=0, le=1, description="Position size as percentage of portfolio")
    quote_quantity: float | None = Field(None, description="Quote quantity for quote-based orders")
    stop_loss: float | None = Field(None, description="Stop loss price")
    stop_loss_pct: float | None = Field(None, ge=0, le=1, description="Stop loss as percentage")
    take_profit: float | None = Field(None, description="Take profit price")
    take_profit_pct: float | None = Field(None, ge=0, le=1, description="Take profit as percentage")
    conditional_price: float | None = Field(None, description="Price level for conditional execution")
    conditional_direction: Literal["above", "below"] | None = Field(None, description="Direction for conditional execution")
    conditional_timeout: int | None = Field(None, description="Timeout in seconds for conditional orders")
    iceberg_quantity: float | None = Field(None, description="Iceberg quantity for iceberg orders")
    client_order_id: str | None = Field(None, description="Client-provided order ID")
    model_confidence: float | None = Field(None, ge=0, le=1, description="ML model confidence score")
    model_features: dict[str, Any] | None = Field(None, description="Features used by ML model")
    llm_reasoning: str | None = Field(None, description="LLM reasoning for the signal")
    llm_alternatives: list[dict[str, Any]] | None = Field(None, description="Alternative actions considered by LLM")
    indicators: dict[str, Any] | None = Field(None, description="Technical indicators and market data")
    rationale: str | None = Field(None, description="Human-readable rationale for the signal")
    meta: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Signal timestamp")

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime:
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                try:
                    timestamp_float = float(v)
                    if 946684800 <= timestamp_float <= 4102444800:
                        return datetime.fromtimestamp(timestamp_float, tz=UTC)
                    else:
                        return datetime.now(UTC)
                except (ValueError, TypeError):
                    return datetime.now(UTC)
        elif isinstance(v, int | float):
            if 946684800 <= v <= 4102444800:
                return datetime.fromtimestamp(v, tz=UTC)
            else:
                return datetime.now(UTC)
        elif isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v
        else:
            return datetime.now(UTC)

    @field_validator("confidence", "model_confidence")
    @classmethod
    def validate_confidence(cls, v: Any) -> float | None:
        if v is not None and (v < 0 or v > 1):
            raise ValueError("Confidence must be between 0 and 1")
        return float(v) if v is not None else None

    @field_validator("position_size_pct", "stop_loss_pct", "take_profit_pct")
    @classmethod
    def validate_percentages(cls, v: Any) -> float | None:
        if v is not None and (v < 0 or v > 1):
            raise ValueError("Percentage must be between 0 and 1")
        return float(v) if v is not None else None

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
        "protected_namespaces": (),
    }
