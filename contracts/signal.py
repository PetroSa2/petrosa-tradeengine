from datetime import datetime
from typing import Literal, Optional, Dict, Any, List
from enum import Enum

from pydantic import BaseModel, Field, validator


class SignalStrength(str, Enum):
    """Signal strength levels"""
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"
    EXTREME = "extreme"


class TimeFrame(str, Enum):
    """Trading timeframes for signal analysis"""
    TICK = "tick"           # Real-time tick data
    MINUTE_1 = "1m"         # 1 minute
    MINUTE_3 = "3m"         # 3 minutes
    MINUTE_5 = "5m"         # 5 minutes
    MINUTE_15 = "15m"       # 15 minutes
    MINUTE_30 = "30m"       # 30 minutes
    HOUR_1 = "1h"           # 1 hour
    HOUR_2 = "2h"           # 2 hours
    HOUR_4 = "4h"           # 4 hours
    HOUR_6 = "6h"           # 6 hours
    HOUR_8 = "8h"           # 8 hours
    HOUR_12 = "12h"         # 12 hours
    DAY_1 = "1d"            # 1 day
    DAY_3 = "3d"            # 3 days
    WEEK_1 = "1w"           # 1 week
    MONTH_1 = "1M"          # 1 month


class OrderType(str, Enum):
    """Supported order types"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    CONDITIONAL_LIMIT = "conditional_limit"  # Execute if price crosses level
    CONDITIONAL_STOP = "conditional_stop"    # Execute if price crosses stop level


class TimeInForce(str, Enum):
    """Order time in force options"""
    GTC = "GTC"  # Good Till Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    GTX = "GTX"  # Good Till Crossing


class StrategyMode(str, Enum):
    """Strategy processing modes"""
    DETERMINISTIC = "deterministic"  # Rule-based processing
    ML_LIGHT = "ml_light"           # Light ML models
    LLM_REASONING = "llm_reasoning" # Full LLM reasoning


class Signal(BaseModel):
    """Enhanced trading signal with advanced features"""
    
    # Core signal information
    strategy_id: str = Field(..., description="Unique identifier for the strategy")
    signal_id: Optional[str] = Field(None, description="Unique identifier for this signal")
    strategy_mode: StrategyMode = Field(StrategyMode.DETERMINISTIC, description="Processing mode for this signal")
    
    # Trading parameters
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    action: Literal["buy", "sell", "hold", "close"] = Field(..., description="Trading action")
    confidence: float = Field(..., ge=0, le=1, description="Signal confidence (0-1)")
    strength: SignalStrength = Field(SignalStrength.MEDIUM, description="Signal strength level")
    
    # Timeframe information
    timeframe: TimeFrame = Field(TimeFrame.HOUR_1, description="Timeframe used for signal analysis")
    
    # Price information
    current_price: float = Field(..., description="Current market price")
    target_price: Optional[float] = Field(None, description="Target execution price")
    
    # Order configuration
    order_type: OrderType = Field(OrderType.MARKET, description="Order type to execute")
    time_in_force: TimeInForce = Field(TimeInForce.GTC, description="Order time in force")
    position_size_pct: Optional[float] = Field(None, ge=0, le=1, description="Position size as percentage of portfolio")
    quote_quantity: Optional[float] = Field(None, description="Quote quantity for quote-based orders")
    
    # Risk management
    stop_loss: Optional[float] = Field(None, description="Stop loss price")
    stop_loss_pct: Optional[float] = Field(None, ge=0, le=1, description="Stop loss as percentage")
    take_profit: Optional[float] = Field(None, description="Take profit price")
    take_profit_pct: Optional[float] = Field(None, ge=0, le=1, description="Take profit as percentage")
    
    # Conditional orders
    conditional_price: Optional[float] = Field(None, description="Price level for conditional execution")
    conditional_direction: Optional[Literal["above", "below"]] = Field(None, description="Direction for conditional execution")
    conditional_timeout: Optional[int] = Field(None, description="Timeout in seconds for conditional orders")
    
    # Advanced order features
    iceberg_quantity: Optional[float] = Field(None, description="Iceberg quantity for iceberg orders")
    client_order_id: Optional[str] = Field(None, description="Client-provided order ID")
    
    # ML/LLM specific fields
    model_confidence: Optional[float] = Field(None, ge=0, le=1, description="ML model confidence score")
    model_features: Optional[Dict[str, Any]] = Field(None, description="Features used by ML model")
    llm_reasoning: Optional[str] = Field(None, description="LLM reasoning for the signal")
    llm_alternatives: Optional[List[Dict[str, Any]]] = Field(None, description="Alternative actions considered by LLM")
    
    # Market indicators
    indicators: Optional[Dict[str, Any]] = Field(None, description="Technical indicators and market data")
    
    # Metadata
    rationale: Optional[str] = Field(None, description="Human-readable rationale for the signal")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    # Timestamp
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Signal timestamp")
    
    @validator('timestamp', pre=True)
    def validate_timestamp(cls, v):
        """Ensure timestamp is timezone-aware"""
        if isinstance(v, str):
            # Parse string to datetime
            try:
                dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
                return dt
            except ValueError:
                # Try parsing without timezone info
                dt = datetime.fromisoformat(v)
                # Make it timezone-aware (UTC)
                return dt.replace(tzinfo=datetime.utcnow().tzinfo)
        elif isinstance(v, datetime):
            # If naive datetime, make it timezone-aware
            if v.tzinfo is None:
                return v.replace(tzinfo=datetime.utcnow().tzinfo)
            return v
        return v
    
    @validator('confidence', 'model_confidence')
    def validate_confidence(cls, v):
        """Validate confidence values"""
        if v is not None and (v < 0 or v > 1):
            raise ValueError("Confidence must be between 0 and 1")
        return v
    
    @validator('position_size_pct', 'stop_loss_pct', 'take_profit_pct')
    def validate_percentages(cls, v):
        """Validate percentage values"""
        if v is not None and (v < 0 or v > 1):
            raise ValueError("Percentage values must be between 0 and 1")
        return v
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
