# Petrosa Trading Engine - Complete Documentation

## 🏗️ System Overview

The Petrosa Trading Engine is a **multi-strategy signal aggregation and intelligent execution platform** that acts as a "trading brain" for cryptocurrency trading. It receives signals from multiple strategies, resolves conflicts, and makes intelligent execution decisions with full audit reliability.

## 📋 Today's Major Changes

### 1. **Enhanced Signal Model** (`contracts/signal.py`)
- **Advanced Signal Structure**: Added support for all Binance order types and advanced trading features
- **Strategy Modes**: Three distinct processing modes (deterministic, ML light, LLM reasoning)
- **Risk Management**: Built-in stop loss, take profit, and position sizing
- **Conditional Orders**: Support for price-triggered execution
- **Timezone Handling**: Proper datetime validation and timezone-aware timestamps

### 2. **Signal Aggregator** (`tradeengine/signal_aggregator.py`)
- **Multi-Strategy Processing**: Handles signals from multiple strategies simultaneously
- **Conflict Resolution**: Intelligent conflict resolution with multiple strategies
- **Risk Management**: Integrated risk checks and position sizing
- **Three Processing Modes**: Deterministic, ML Light, and LLM Reasoning

### 3. **Position Manager** (`tradeengine/position_manager.py`)
- **Real-time Position Tracking**: Tracks all positions with P&L calculation
- **Risk Limits**: Enforces position size and portfolio exposure limits
- **Portfolio Management**: Comprehensive portfolio summary and risk monitoring

### 4. **Order Manager** (`tradeengine/order_manager.py`)
- **Advanced Order Tracking**: Manages active, conditional, and historical orders
- **Conditional Execution**: Monitors price conditions for conditional orders
- **Order Lifecycle**: Complete order lifecycle management

### 5. **Enhanced Dispatcher** (`tradeengine/dispatcher.py`)
- **Intelligent Execution**: Orchestrates the entire trading process
- **Risk Integration**: Integrated risk management and position tracking
- **Audit Logging**: Full audit trail for all trading activities

### 6. **Audit System** (`shared/audit.py`)
- **MongoDB Integration**: All events logged to MongoDB for reliability
- **Fail-Safe Trading**: Real trading disabled if audit logging unavailable
- **Complete Audit Trail**: Every signal, order, position, and error logged

## 🔄 Signal Flow & Conflict Resolution

### **Signal Reception Flow**

```
Strategy A (Momentum)     Strategy B (Mean Rev)     Strategy C (Arbitrage)
      │                          │                          │
      └──────────────────────────┼──────────────────────────┘
                                 │
                    ┌─────────────▼─────────────┐
                    │     /trade endpoint      │
                    │   Signal Aggregator      │
                    │  - Conflict Resolution    │
                    │  - Risk Management        │
                    │  - Position Sizing        │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Signal Processor        │
                    │  ├─ Deterministic         │
                    │  ├─ ML Light              │
                    │  └─ LLM Reasoning         │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Order Manager           │
                    │  - Smart Execution        │
                    │  - Stop/Take Profit       │
                    │  - Order Tracking         │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Position Manager        │
                    │  - Risk Limits            │
                    │  - P&L Tracking           │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Binance Exchange        │
                    └───────────────────────────┘
```

### **Conflict Resolution Strategies**

The system supports multiple conflict resolution strategies when opposing signals are received for the same symbol:

#### **1. Strongest Wins (Default)**
```python
# Calculate signal strength for each signal
new_strength = calculate_signal_strength(new_signal)
existing_strength = max(calculate_signal_strength(s) for s in opposing_signals)

if new_strength > existing_strength:
    # Cancel existing signals and execute new one
    cancel_opposing_signals(symbol)
    execute_signal(new_signal)
else:
    # Reject weaker signal
    reject_signal(new_signal)
```

**Signal Strength Calculation:**
```python
def calculate_signal_strength(signal: Signal) -> float:
    base_strength = signal.confidence

    # Apply strategy weight
    strategy_weight = strategy_weights.get(signal.strategy_id, 1.0)
    weighted_strength = base_strength * strategy_weight

    # Apply strength multiplier
    strength_multipliers = {
        SignalStrength.WEAK: 0.5,
        SignalStrength.MEDIUM: 1.0,
        SignalStrength.STRONG: 1.5,
        SignalStrength.EXTREME: 2.0
    }
    multiplier = strength_multipliers.get(signal.strength, 1.0)

    # Apply mode-specific adjustments
    mode_multipliers = {
        StrategyMode.DETERMINISTIC: 1.0,
        StrategyMode.ML_LIGHT: 1.2,
        StrategyMode.LLM_REASONING: 1.5
    }
    mode_multiplier = mode_multipliers.get(signal.strategy_mode, 1.0)

    return weighted_strength * multiplier * mode_multiplier
```

#### **2. First Come First Served**
```python
# Simply reject new signals if there are existing opposing signals
if opposing_signals:
    return {"status": "rejected", "reason": "Signal conflict - FCFS policy"}
```

#### **3. Manual Review**
```python
# Flag for manual review
return {"status": "pending_review", "reason": "Signal conflict requires manual review"}
```

#### **4. Weighted Average**
```python
# Calculate weighted average of all signals
all_signals = opposing_signals + [new_signal]
total_weight = 0
weighted_action = 0  # -1 for sell, 0 for hold, 1 for buy

for signal in all_signals:
    weight = calculate_signal_strength(signal)
    action_value = {"sell": -1, "hold": 0, "buy": 1}.get(signal.action, 0)
    weighted_action += action_value * weight
    total_weight += weight

if total_weight > 0:
    final_action_value = weighted_action / total_weight

    # Determine final action
    if final_action_value > 0.3:
        final_action = "buy"
    elif final_action_value < -0.3:
        final_action = "sell"
    else:
        final_action = "hold"

    # Update signal with aggregated action
    signal.action = final_action
    return {"status": "executed", "reason": "Weighted average conflict resolution"}
```

## 🎛️ Strategy Processing Modes

### **1. Deterministic Mode**
- **Description**: Rule-based, deterministic logic
- **Use Case**: Traditional trading strategies with clear rules
- **Features**:
  - Confidence threshold checks (default: 0.6)
  - Simple conflict resolution
  - Position size scaling by confidence
  - Risk management rules

**Example Signal:**
```json
{
  "strategy_id": "momentum_v1",
  "strategy_mode": "deterministic",
  "symbol": "BTCUSDT",
  "action": "buy",
  "confidence": 0.85,
  "current_price": 45000.0,
  "order_type": "limit",
  "position_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05
}
```

### **2. ML Light Mode**
- **Description**: Light machine learning models
- **Use Case**: ML-powered strategies
- **Features**:
  - Feature extraction from signals
  - ML model prediction
  - Confidence scoring
  - Model-based position sizing

**Example Signal:**
```json
{
  "strategy_id": "ml_momentum_v2",
  "strategy_mode": "ml_light",
  "symbol": "ETHUSDT",
  "action": "buy",
  "confidence": 0.78,
  "model_confidence": 0.82,
  "model_features": {
    "rsi": 65,
    "macd": "bullish",
    "volume_ratio": 1.2
  },
  "current_price": 3000.0
}
```

### **3. LLM Reasoning Mode**
- **Description**: Full LLM reasoning and decision making
- **Use Case**: Complex strategies requiring reasoning
- **Features**:
  - Context-aware reasoning
  - Alternative analysis
  - Risk assessment
  - Conservative sizing

**Example Signal:**
```json
{
  "strategy_id": "llm_adaptive_v1",
  "strategy_mode": "llm_reasoning",
  "symbol": "BTCUSDT",
  "action": "buy",
  "confidence": 0.72,
  "llm_reasoning": "Market shows strong momentum with volume confirmation. Risk-reward ratio favorable.",
  "llm_alternatives": [
    {"action": "hold", "reason": "Wait for better entry"},
    {"action": "buy", "reason": "Strong momentum confirmed"}
  ],
  "current_price": 45000.0
}
```

## 🔄 Trade Execution Flow

### **Step 1: Signal Reception**
```python
# Strategy sends signal to /trade endpoint
POST /trade
{
  "strategy_id": "momentum_v1",
  "strategy_mode": "deterministic",
  "symbol": "BTCUSDT",
  "action": "buy",
  "confidence": 0.85,
  "current_price": 45000.0,
  "order_type": "limit",
  "position_size_pct": 0.1
}
```

### **Step 2: Signal Aggregation**
```python
# Signal aggregator processes the signal
result = await signal_aggregator.process_signal(signal)

# Based on strategy mode:
if signal.strategy_mode == StrategyMode.DETERMINISTIC:
    result = await deterministic_processor.process(signal)
elif signal.strategy_mode == StrategyMode.ML_LIGHT:
    result = await ml_processor.process(signal)
elif signal.strategy_mode == StrategyMode.LLM_REASONING:
    result = await llm_processor.process(signal)
```

### **Step 3: Conflict Resolution**
```python
# Check for conflicting signals
conflict_result = aggregator._resolve_conflicts(signal)

if conflict_result["has_conflict"]:
    # Apply resolution strategy
    if resolution_strategy == "strongest_wins":
        # Execute stronger signal, cancel weaker
        pass
    elif resolution_strategy == "weighted_average":
        # Calculate weighted average of all signals
        pass
```

### **Step 4: Order Creation**
```python
# Convert signal to order with calculated parameters
trade_order = dispatcher._signal_to_order(signal, order_params)

# Order includes:
# - Position size scaled by confidence
# - Stop loss and take profit levels
# - Order type and time in force
# - Risk management parameters
```

### **Step 5: Risk Validation**
```python
# Check position size limits
if not await position_manager.check_position_limits(order):
    return {"status": "rejected", "reason": "Position size limit exceeded"}

# Check daily loss limits
if not await position_manager.check_daily_loss_limits():
    return {"status": "rejected", "reason": "Daily loss limit exceeded"}

# Check portfolio exposure
if current_exposure > max_portfolio_exposure_pct:
    return {"status": "rejected", "reason": "Portfolio exposure limit exceeded"}
```

### **Step 6: Order Execution**
```python
# Execute based on order type
if order.type == "market":
    result = await binance_exchange.execute_market_order(order)
elif order.type == "limit":
    result = await binance_exchange.execute_limit_order(order)
elif order.type == "stop":
    result = await binance_exchange.execute_stop_order(order)
# ... other order types
```

### **Step 7: Position Update**
```python
# Update position tracking
await position_manager.update_position(order, result)

# Calculate new position:
# - Average price
# - Quantity
# - Realized/unrealized P&L
# - Total cost and value
```

### **Step 8: Order Tracking**
```python
# Track order for monitoring
await order_manager.track_order(order, result)

# For conditional orders:
if order.type in ["conditional_limit", "conditional_stop"]:
    await order_manager._setup_conditional_order(order, result)
```

## 🛡️ Risk Management

### **Position Limits**
```python
# Maximum position size per symbol
MAX_POSITION_SIZE_PCT = 0.1  # 10% of portfolio

# Portfolio exposure limit
MAX_PORTFOLIO_EXPOSURE_PCT = 0.5  # 50% of portfolio

# Daily loss limit
MAX_DAILY_LOSS_PCT = 0.05  # 5% of portfolio
```

### **Signal Validation**
```python
# Confidence thresholds
MIN_CONFIDENCE = 0.6  # Minimum confidence for execution

# Signal age limits
MAX_SIGNAL_AGE_SECONDS = 300  # 5 minutes

# Strategy weight validation
STRATEGY_WEIGHTS = {
    "momentum_v1": 1.0,
    "mean_reversion_v1": 0.8,
    "arbitrage_v1": 1.2
}
```

### **Order Validation**
```python
# Price validation
if order.type in ["limit", "stop_limit", "take_profit_limit"]:
    if order.target_price is None:
        raise ValueError("Target price required for limit orders")

# Quantity validation
if order.amount <= 0:
    raise ValueError("Order amount must be positive")

# Side validation
if order.side not in ["buy", "sell"]:
    raise ValueError(f"Invalid order side: {order.side}")
```

## 📊 Audit Logging System

### **MongoDB Collections**
- **`signals`**: All incoming signals and processing results
- **`orders`**: All order executions and results
- **`positions`**: All position updates and changes
- **`errors`**: All system errors and exceptions
- **`events`**: System events and conditional order activities

### **Audit Log Structure**
```python
# Signal Log
{
    "type": "signal",
    "status": "executed",  # received, processing, executed, rejected, expired
    "signal": {...},  # Full signal data
    "extra": {...},  # Additional context
    "timestamp": datetime.utcnow()
}

# Order Log
{
    "type": "order",
    "status": "executed",  # executed, rejected, failed, cancelled
    "order": {...},  # Full order data
    "result": {...},  # Execution result
    "extra": {...},  # Additional context
    "timestamp": datetime.utcnow()
}

# Position Log
{
    "type": "position",
    "status": "updated",  # updated, opened, closed
    "position": {...},  # Position data
    "extra": {...},  # Additional context
    "timestamp": datetime.utcnow()
}

# Error Log
{
    "type": "error",
    "error": "Error message",
    "context": {...},  # Error context
    "timestamp": datetime.utcnow()
}
```

### **Fail-Safe Trading**
```python
# Only allow real trading if audit logging is available
if not audit_logger.enabled or not audit_logger.connected:
    logger.error("Audit logging unavailable, refusing real trade execution. Only simulation allowed.")
    order.simulate = True
```

### **Health Monitoring**
```python
# Health endpoint includes audit logger status
{
    "status": "healthy",  # or "degraded" if audit logging unavailable
    "components": {
        "audit_logger": {
            "enabled": true,
            "connected": true
        }
    },
    "warnings": [
        "Audit logging is not available. Real trading is disabled. Only simulation is allowed."
    ]
}
```

## 🔧 API Endpoints

### **Signal Processing**
- `POST /trade` - Process trading signals from strategies
- `GET /signals/summary` - Get signal aggregation summary
- `GET /signals/active` - Get active signals
- `POST /signals/strategy/{id}/weight` - Set strategy weight

### **Order Management**
- `POST /order` - Place advanced orders directly
- `GET /orders` - Get all orders
- `GET /orders/{id}` - Get specific order
- `DELETE /orders/{id}` - Cancel order

### **Position Management**
- `GET /positions` - Get all positions
- `GET /positions/{symbol}` - Get specific position

### **Account & Market Data**
- `GET /account` - Get account information
- `GET /price/{symbol}` - Get current price

### **System Health**
- `GET /health` - Detailed health check with audit status
- `GET /ready` - Readiness probe (fails if audit logging unavailable)
- `GET /live` - Liveness probe

## 🚀 Getting Started

### **1. Environment Setup**
```bash
# Install dependencies
make setup

# Configure environment
cp .env.example .env
# Edit .env with your MongoDB and Binance settings
```

### **2. Start the Service**
```bash
# Run locally
make run

# Or with Docker
make run-docker
```

### **3. Test Signal Processing**
```bash
# Deterministic signal
curl -X POST http://localhost:8000/trade \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "test_strategy",
    "strategy_mode": "deterministic",
    "symbol": "BTCUSDT",
    "action": "buy",
    "confidence": 0.8,
    "current_price": 45000.0,
    "order_type": "limit",
    "position_size_pct": 0.1
  }'
```

### **4. Monitor System**
```bash
# Check health
curl http://localhost:8000/health

# Get signal summary
curl http://localhost:8000/signals/summary

# Get positions
curl http://localhost:8000/positions
```

## 🔒 Safety Features

### **Audit Reliability**
- All trading activities are logged to MongoDB
- No real trades executed without audit logging
- Complete audit trail for compliance and debugging

### **Risk Management**
- Position size limits enforced
- Portfolio exposure monitoring
- Daily loss limits
- Real-time risk validation

### **Conflict Resolution**
- Multiple resolution strategies available
- Signal strength calculation
- Strategy weighting system
- Manual review option for complex conflicts

### **Fail-Safe Operation**
- Simulation mode when audit logging unavailable
- Health checks for all critical components
- Graceful degradation of functionality

This system provides a robust, auditable, and safe foundation for multi-strategy cryptocurrency trading with intelligent signal aggregation and conflict resolution.
