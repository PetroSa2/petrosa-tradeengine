# Timeframe-Based Conflict Resolution

## Overview

The Petrosa Trading Engine now supports advanced timeframe-based conflict resolution for multi-strategy signal aggregation. This feature allows the system to intelligently handle conflicting signals from different timeframes and strategies, prioritizing higher timeframe signals over lower timeframe signals when appropriate.

## Timeframe Hierarchy

The system supports a comprehensive range of timeframes, ordered by significance:

| Timeframe | Value | Numeric Weight | Description |
|-----------|-------|----------------|-------------|
| TICK | tick | 0.3 | Real-time tick data |
| 1m | 1m | 0.5 | 1 minute |
| 3m | 3m | 0.6 | 3 minutes |
| 5m | 5m | 0.7 | 5 minutes |
| 15m | 15m | 0.8 | 15 minutes |
| 30m | 30m | 0.9 | 30 minutes |
| 1h | 1h | 1.0 | 1 hour |
| 2h | 2h | 1.1 | 2 hours |
| 4h | 4h | 1.2 | 4 hours |
| 6h | 6h | 1.3 | 6 hours |
| 8h | 8h | 1.4 | 8 hours |
| 12h | 12h | 1.5 | 12 hours |
| 1d | 1d | 1.6 | 1 day |
| 3d | 3d | 1.7 | 3 days |
| 1w | 1w | 1.8 | 1 week |
| 1M | 1M | 2.0 | 1 month |

## Conflict Resolution Strategies

### 1. Higher Timeframe Wins (`higher_timeframe_wins`)

**Description**: Higher timeframe signals automatically override lower timeframe signals when there's a conflict.

**Use Case**: When a 4-hour signal conflicts with a 1-minute signal, the 4-hour signal takes precedence.

**Configuration**:
```bash
export TIMEFRAME_CONFLICT_RESOLUTION="higher_timeframe_wins"
```

**Example**:
```python
# 1-minute signal (buy)
signal_1m = Signal(
    strategy_id="momentum_strategy",
    action="buy",
    timeframe=TimeFrame.MINUTE_1,
    confidence=0.8,
    # ... other fields
)

# 4-hour signal (sell) - will win the conflict
signal_4h = Signal(
    strategy_id="mean_reversion_strategy",
    action="sell",
    timeframe=TimeFrame.HOUR_4,
    confidence=0.7,
    # ... other fields
)
```

### 2. Timeframe Weighted (`timeframe_weighted`)

**Description**: Uses weighted averages considering timeframe strength, strategy weight, and confidence.

**Use Case**: More nuanced conflict resolution that considers multiple factors.

**Configuration**:
```bash
export TIMEFRAME_CONFLICT_RESOLUTION="timeframe_weighted"
```

**Calculation**:
```
timeframe_strength = confidence × timeframe_weight × strategy_weight × mode_multiplier
```

### 3. Standard Strategies

The system also supports traditional conflict resolution strategies:

- **Strongest Wins**: Based on overall signal strength
- **First Come First Served**: Based on signal arrival time
- **Manual Review**: Requires human intervention
- **Weighted Average**: Combines all signals

## Signal Model Integration

### Timeframe Field

All signals now include a required `timeframe` field:

```python
from contracts.signal import Signal, TimeFrame

signal = Signal(
    strategy_id="my_strategy",
    symbol="BTCUSDT",
    action="buy",
    confidence=0.8,
    timeframe=TimeFrame.HOUR_1,  # Required field
    current_price=45000.0,
    # ... other fields
)
```

### API Endpoint Schema

The `/trade` endpoint now accepts signals with timeframe information:

```json
{
  "strategy_id": "momentum_strategy",
  "symbol": "BTCUSDT",
  "action": "buy",
  "confidence": 0.8,
  "strength": "strong",
  "timeframe": "1h",
  "current_price": 45000.0,
  "order_type": "market",
  "time_in_force": "GTC",
  "strategy_mode": "deterministic",
  "position_size_pct": 0.05,
  "stop_loss": 44000.0,
  "take_profit": 46000.0,
  "rationale": "Strong momentum on 1h timeframe",
  "meta": {
    "strategy_type": "momentum",
    "timeframe": "1h",
    "indicators": {
      "rsi": 65,
      "macd": "bullish"
    }
  }
}
```

## Configuration

### Environment Variables

```bash
# Conflict resolution strategy
export TIMEFRAME_CONFLICT_RESOLUTION="higher_timeframe_wins"

# Timeframe weights (optional - defaults provided)
export TICK_WEIGHT="0.3"
export MINUTE_1_WEIGHT="0.5"
export MINUTE_5_WEIGHT="0.7"
export HOUR_1_WEIGHT="1.0"
export HOUR_4_WEIGHT="1.2"
export DAY_1_WEIGHT="1.6"
export WEEK_1_WEIGHT="1.8"
export MONTH_1_WEIGHT="2.0"

# Strategy weights
export MOMENTUM_STRATEGY_WEIGHT="1.0"
export MEAN_REVERSION_STRATEGY_WEIGHT="0.8"
export ML_STRATEGY_WEIGHT="1.2"
export LLM_STRATEGY_WEIGHT="1.5"
```

### Kubernetes Configuration

```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: petrosa-config
  namespace: petrosa-apps
data:
  TIMEFRAME_CONFLICT_RESOLUTION: "higher_timeframe_wins"
  TICK_WEIGHT: "0.3"
  HOUR_1_WEIGHT: "1.0"
  DAY_1_WEIGHT: "1.6"
  MOMENTUM_STRATEGY_WEIGHT: "1.0"
  LLM_STRATEGY_WEIGHT: "1.5"
```

## Usage Examples

### 1. Basic Timeframe Conflict

```python
import asyncio
from contracts.signal import Signal, TimeFrame, StrategyMode

# Create conflicting signals
signal_1m = Signal(
    strategy_id="momentum_strategy",
    symbol="BTCUSDT",
    action="buy",
    confidence=0.8,
    timeframe=TimeFrame.MINUTE_1,
    current_price=45000.0,
    # ... other required fields
)

signal_4h = Signal(
    strategy_id="mean_reversion_strategy",
    symbol="BTCUSDT",
    action="sell",
    confidence=0.7,
    timeframe=TimeFrame.HOUR_4,
    current_price=45000.0,
    # ... other required fields
)

# The 4-hour signal will win the conflict
```

### 2. Advanced Order Types with Timeframes

```python
# Stop limit order with timeframe
stop_limit_signal = Signal(
    strategy_id="breakout_strategy",
    symbol="BTCUSDT",
    action="buy",
    confidence=0.85,
    timeframe=TimeFrame.HOUR_1,
    current_price=45000.0,
    order_type=OrderType.STOP_LIMIT,
    conditional_price=45500.0,
    conditional_direction="above",
    # ... other fields
)
```

### 3. LLM Reasoning with Timeframe Context

```python
llm_signal = Signal(
    strategy_id="llm_strategy",
    symbol="BTCUSDT",
    action="buy",
    confidence=0.9,
    timeframe=TimeFrame.DAY_1,
    current_price=45000.0,
    strategy_mode=StrategyMode.LLM_REASONING,
    llm_reasoning="Based on daily timeframe analysis, strong bullish momentum detected...",
    # ... other fields
)
```

## Testing

### Unit Tests

```bash
# Run timeframe-specific tests
pytest tests/test_dispatcher.py::test_timeframe_conflict_resolution
pytest tests/test_dispatcher.py::test_timeframe_strength_calculation
pytest tests/test_dispatcher.py::test_timeframe_numeric_values
```

### Integration Testing

```bash
# Run the signal publishing demo
python examples/publish_signal.py
```

This will demonstrate:
- Different timeframe signals
- Conflict resolution scenarios
- Advanced order types
- Strategy mode variations

## Monitoring and Metrics

### Signal Aggregation Metrics

The system provides metrics for timeframe-based conflict resolution:

- `signal_conflicts_total`: Total number of signal conflicts
- `timeframe_conflicts_total`: Conflicts resolved by timeframe
- `timeframe_weights`: Current timeframe weight configuration

### Logging

Timeframe conflict resolution is logged with detailed information:

```
INFO: Processing signal from momentum_strategy: buy BTCUSDT (timeframe: 1m)
INFO: Signal conflict detected - higher timeframe signal won (4h vs 1m)
INFO: Higher timeframe signal won conflict (4h vs 1m)
```

## Best Practices

### 1. Timeframe Selection

- **Short-term strategies**: Use 1m, 5m, 15m timeframes
- **Medium-term strategies**: Use 1h, 4h, 6h timeframes
- **Long-term strategies**: Use 1d, 1w, 1M timeframes

### 2. Conflict Resolution Strategy

- **Higher timeframe wins**: Best for trend-following strategies
- **Timeframe weighted**: Best for mean reversion strategies
- **Manual review**: Best for high-stakes decisions

### 3. Signal Design

- Always include appropriate timeframe
- Consider timeframe when setting confidence levels
- Use timeframe-appropriate indicators
- Include timeframe context in rationale

### 4. Risk Management

- Higher timeframes generally indicate stronger signals
- Consider timeframe when setting position sizes
- Use timeframe-appropriate stop losses and take profits

## Troubleshooting

### Common Issues

1. **Missing Timeframe Field**
   ```
   ValueError: 1 validation error for Signal
   timeframe: field required
   ```

2. **Invalid Timeframe Value**
   ```
   ValueError: 1 validation error for Signal
   timeframe: value is not a valid enumeration member
   ```

3. **Configuration Issues**
   ```
   WARNING: Timeframe conflict resolution not configured, using default
   ```

### Debugging

Enable debug logging for timeframe conflict resolution:

```bash
export LOG_LEVEL="DEBUG"
```

This will provide detailed logs about:
- Signal processing steps
- Conflict detection
- Resolution decisions
- Timeframe strength calculations

## Migration Guide

### From Previous Version

1. **Update Signal Creation**: Add `timeframe` field to all signals
2. **Update Tests**: Modify test cases to include timeframe
3. **Update Configuration**: Set timeframe conflict resolution strategy
4. **Update Documentation**: Reference new timeframe features

### Backward Compatibility

- Signals without timeframe will use default `HOUR_1`
- Existing conflict resolution strategies still work
- No breaking changes to API endpoints

## Future Enhancements

### Planned Features

1. **Dynamic Timeframe Weights**: Adjust weights based on market conditions
2. **Timeframe-Specific Strategies**: Specialized processing per timeframe
3. **Multi-Timeframe Analysis**: Combine signals from multiple timeframes
4. **Timeframe Performance Tracking**: Monitor success rates by timeframe

### Roadmap

- **v1.2.0**: Dynamic timeframe weights
- **v1.3.0**: Multi-timeframe signal fusion
- **v1.4.0**: Timeframe-specific risk management
