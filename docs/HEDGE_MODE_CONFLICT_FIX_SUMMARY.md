# Hedge Mode Conflict Resolution Fix - Implementation Summary

**Date**: October 21, 2025
**Status**: ✅ Implementation Complete
**Version**: 1.0.0

## Overview

Successfully fixed critical issues in the tradeengine's conflict management system to properly support hedge mode trading. The system now correctly handles:
- Simultaneous LONG and SHORT positions on the same symbol (hedge mode)
- Multiple orders in the same direction from different strategies
- Position tracking by (symbol, position_side) tuple

## Critical Problems Fixed

### 1. Hedge Mode Conflicts ✅

**Problem**: The conflict resolution logic treated opposite directions (BUY/SELL) as conflicts, preventing legitimate hedge positions.

**Files Modified**:
- `tradeengine/signal_aggregator.py`
  - DeterministicProcessor
  - MLProcessor
  - LLMProcessor

**Solution**:
- Added `position_mode` awareness to conflict detection
- In hedge mode: opposite directions are NOT conflicts
- In one-way mode: opposite directions remain conflicts
- Configurable via `POSITION_MODE_AWARE_CONFLICTS`

**Example**:
```python
# Before: REJECTED in hedge mode
# Signal 1: BUY BTCUSDT
# Signal 2: SELL BTCUSDT --> REJECTED (conflict)

# After: ALLOWED in hedge mode
# Signal 1: BUY BTCUSDT --> Creates LONG position
# Signal 2: SELL BTCUSDT --> Creates SHORT position (no conflict!)
```

### 2. Same-Direction Signal Handling ✅

**Problem**: No clear strategy for handling multiple orders in the same direction (e.g., two BUY signals from different strategies).

**Solution**: Added three configurable strategies:

1. **accumulate** (default): Allow multiple strategies to build positions
2. **strongest_wins**: Only execute highest confidence signal
3. **reject_duplicates**: Reject subsequent same-direction signals

**Configuration**: `SAME_DIRECTION_CONFLICT_RESOLUTION`

**Example**:
```python
# Accumulate mode:
# Strategy A: BUY 0.001 BTC @ $45,000 --> Executes
# Strategy B: BUY 0.002 BTC @ $46,000 --> Executes (accumulate!)
# Result: Total position 0.003 BTC

# Strongest wins mode:
# Strategy A: BUY confidence=0.8 --> Executes
# Strategy B: BUY confidence=0.7 --> REJECTED (weaker)
```

### 3. Position Manager Keys ✅

**Problem**: Position manager tracked positions by symbol only, preventing hedge mode support.

**Files Modified**:
- `tradeengine/position_manager.py`

**Solution**:
- Changed position keys from `symbol` to `(symbol, position_side)` tuples
- Updated all MongoDB operations to include `position_side`
- Added backward compatibility for get_position() method
- New method: `get_positions_by_symbol()` for hedge mode queries

**Example**:
```python
# Before (BROKEN in hedge mode):
positions = {
    "BTCUSDT": {...}  # Only one position per symbol
}

# After (WORKS in hedge mode):
positions = {
    ("BTCUSDT", "LONG"): {...},   # LONG position
    ("BTCUSDT", "SHORT"): {...},  # SHORT position (separate!)
}
```

## New Configuration Parameters

### 1. `POSITION_MODE_AWARE_CONFLICTS`
- **Type**: Boolean
- **Default**: `true`
- **Purpose**: Enable hedge mode awareness in conflict resolution
- **Impact**: When true with hedge mode, allows simultaneous LONG/SHORT positions

### 2. `SAME_DIRECTION_CONFLICT_RESOLUTION`
- **Type**: String
- **Default**: `"accumulate"`
- **Allowed Values**: `accumulate`, `strongest_wins`, `reject_duplicates`
- **Purpose**: Control how multiple same-direction signals are handled
- **Impact**: Determines if strategies can build positions together or compete

## Files Modified

### Core Logic
1. **tradeengine/signal_aggregator.py** (207 lines, +65 new logic)
   - `DeterministicProcessor`: Added `_get_conflicting_signals()` and `_handle_same_direction_signals()`
   - `MLProcessor`: Added hedge mode awareness
   - `LLMProcessor`: Added hedge mode awareness + context info

2. **tradeengine/position_manager.py** (451 lines, +50 modifications)
   - Changed all position tracking to use `(symbol, position_side)` tuples
   - Updated MongoDB operations for hedge mode
   - Added `get_positions_by_symbol()` method

3. **shared/constants.py** (+7 lines)
   - Added `POSITION_MODE_AWARE_CONFLICTS`
   - Added `SAME_DIRECTION_CONFLICT_RESOLUTION`

4. **tradeengine/defaults.py** (+50 lines)
   - Added configuration documentation for new parameters

### Tests
5. **tests/test_hedge_mode_conflicts.py** (NEW, 520 lines)
   - 13 comprehensive test cases
   - Covers hedge mode, same-direction handling, position tracking
   - Integration tests for complex scenarios

## Test Coverage

```bash
# All tests pass ✅
pytest tests/test_hedge_mode_conflicts.py -v

# Test categories:
- Hedge mode conflict tests (3 tests)
- Same-direction signal tests (3 tests)
- Position manager tests (3 tests)
- ML/LLM processor tests (3 tests)
- Integration tests (1 test)
```

### Key Test Scenarios

1. **Opposite directions NOT conflict in hedge mode**
   - BUY and SELL on same symbol both execute
   - Creates separate LONG and SHORT positions

2. **Opposite directions DO conflict in one-way mode**
   - BUY and SELL on same symbol conflict
   - Second signal rejected

3. **Same-direction accumulate mode**
   - Multiple BUY signals from different strategies execute
   - Position quantities accumulate

4. **Position tracking by tuple**
   - LONG and SHORT positions tracked separately
   - Position keys are `(symbol, position_side)` tuples

5. **Complex integration scenario**
   - Multiple signals in hedge mode
   - Position accumulation working correctly
   - Separate LONG/SHORT position tracking

## Usage Examples

### 1. Enable Hedge Mode Conflict Resolution

```bash
# Environment variables
export POSITION_MODE="hedge"
export POSITION_MODE_AWARE_CONFLICTS="true"
export SAME_DIRECTION_CONFLICT_RESOLUTION="accumulate"
```

### 2. Query Positions in Hedge Mode

```python
from tradeengine.position_manager import position_manager

# Get specific position by symbol and side
long_position = position_manager.get_position("BTCUSDT", "LONG")
short_position = position_manager.get_position("BTCUSDT", "SHORT")

# Get all positions for a symbol (hedge mode)
all_btc_positions = position_manager.get_positions_by_symbol("BTCUSDT")
# Returns: [{"symbol": "BTCUSDT", "position_side": "LONG", ...},
#           {"symbol": "BTCUSDT", "position_side": "SHORT", ...}]

# Backward compatible (returns first found)
position = position_manager.get_position("BTCUSDT")
```

### 3. Signal Processing with Hedge Mode

```python
from tradeengine.signal_aggregator import DeterministicProcessor

processor = DeterministicProcessor()

# Signal 1: BUY
buy_signal = Signal(
    strategy_id="momentum_v1",
    symbol="BTCUSDT",
    action="buy",
    confidence=0.8
)
result1 = await processor.process(buy_signal, {})
# Status: "executed" ✅

# Signal 2: SELL (opposite direction)
sell_signal = Signal(
    strategy_id="mean_reversion_v1",
    symbol="BTCUSDT",
    action="sell",
    confidence=0.75
)
result2 = await processor.process(sell_signal, {buy_signal.strategy_id: buy_signal})
# Status: "executed" ✅ (no conflict in hedge mode!)
```

## Migration Guide

### For Existing Deployments

1. **Database Migration**: Existing positions will be migrated automatically
   - Old positions without `position_side` default to "LONG"
   - New positions include `position_side` field

2. **Configuration**: Add new environment variables to deployment
   ```yaml
   # k8s/deployment.yaml or k8s/configmap.yaml
   - name: POSITION_MODE_AWARE_CONFLICTS
     value: "true"
   - name: SAME_DIRECTION_CONFLICT_RESOLUTION
     value: "accumulate"
   ```

3. **Backward Compatibility**:
   - `get_position(symbol)` still works (returns first found)
   - Use `get_position(symbol, position_side)` for explicit queries
   - Use `get_positions_by_symbol(symbol)` for all hedge positions

## Performance Impact

- **Negligible**: Only adds position_side checks in conflict resolution
- **No database schema changes**: position_side already exists
- **Memory**: Minimal increase (tuple keys vs string keys)

## Known Limitations

1. **Position Contribution Tracking**: Not yet implemented
   - Multiple strategies building one position cannot be attributed individually
   - Future enhancement planned (see PHASE 2 in plan document)

2. **Strategy Position vs Exchange Position**: Not separated
   - Strategy's TP/SL triggers close strategy position, not exchange position
   - Future enhancement for better analytics (see user conversation)

## Deployment Checklist

- [x] Code changes implemented
- [x] Tests passing (13/13)
- [x] Configuration documented
- [x] No linting errors
- [x] Backward compatibility maintained
- [ ] Update k8s/configmap.yaml with new env vars
- [ ] Deploy to staging for validation
- [ ] Monitor hedge mode positions in Grafana
- [ ] Deploy to production

## Monitoring

### Grafana Queries

```promql
# Count positions by position_side
sum by (position_side) (tradeengine_positions_opened_total)

# Hedge mode activity
rate(tradeengine_positions_opened_total{position_side="SHORT"}[5m])
rate(tradeengine_positions_opened_total{position_side="LONG"}[5m])

# Same-direction signal handling
tradeengine_signals_processed_total{status="executed"}
tradeengine_signals_processed_total{status="rejected"}
```

### Log Messages to Monitor

```
✅ SIGNAL VALIDATED: [strategy] | Converting to order
⚠️  SIGNAL REJECTED: [strategy] | Reason: [reason]
📊 Updated position for BTCUSDT LONG
📊 Updated position for BTCUSDT SHORT
```

## References

- [Hedge Mode Position Tracking](HEDGE_MODE_POSITION_TRACKING.md)
- [Conflict Resolution Documentation](TIMEFRAME_CONFLICT_RESOLUTION.md)
- [Trading Engine Documentation](TRADING_ENGINE_DOCUMENTATION.md)
- [Test File](../tests/test_hedge_mode_conflicts.py)

## Next Steps (Future Enhancements)

### Phase 2: Position Contribution Tracking
- Track which strategies contributed to each position
- Attribute PnL to each contributing strategy
- Enable per-strategy analytics in hedge mode

### Phase 3: Strategy Position vs Exchange Position Separation
- Separate virtual strategy positions from physical exchange positions
- Track strategy TP/SL triggers independently
- Enable granular strategy performance analytics

## Conclusion

The hedge mode conflict resolution fix is complete and ready for deployment. The system now properly supports:

✅ Hedge mode with simultaneous LONG/SHORT positions
✅ Configurable same-direction signal handling
✅ Proper position tracking by (symbol, position_side)
✅ Backward compatibility maintained
✅ Comprehensive test coverage

The changes are isolated, well-tested, and have minimal performance impact. The system is now production-ready for hedge mode trading.
