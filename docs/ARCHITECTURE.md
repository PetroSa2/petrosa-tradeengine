# Petrosa Trading Engine - Architecture Overview

## 🏗️ System Architecture

The Petrosa Trading Engine is a **multi-strategy signal aggregation and intelligent execution platform** that acts as a "trading brain" for cryptocurrency trading. It receives signals from multiple strategies, resolves conflicts, and makes intelligent execution decisions.

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Strategy A    │    │   Strategy B    │    │   Strategy C    │
│  (Real-time)    │    │   (Mean Rev)    │    │   (Arbitrage)   │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────┬───────────┴──────────┬───────────┘
                     │                      │
                     ▼                      ▼
          ┌──────────────────────────────────────────────┐
          │                petrosa-cio                   │
          │             (Interception Layer)             │
          │      [intent.>]  ──▶  [signals.trading]      │
          └─────────────────────┬────────────────────────┘
                                │
                                ▼ APPROVED SIGNALS
                    ┌───────────────────────────┐
                    │     tradeengine          │
                    │   (THIS SERVICE)         │
                    │  - Signal Validation      │
                    │  - Risk Management        │
                    │  - Position Sizing        │
                    │  - Smart Execution        │
                    └─────────────┬─────────────┘
                                 │
                     ▼
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

## 🎯 Core Components

### 1. **Signal Aggregator** (`tradeengine/signal_aggregator.py`)
- **Purpose**: Receives signals from multiple strategies and makes intelligent execution decisions
- **Features**:
  - Multi-strategy signal processing
  - Conflict resolution (strongest wins, weighted average, etc.)
  - Risk management integration
  - Signal strength calculation
  - Strategy weighting system

### 2. **Signal Processors** (Three Modes)

#### **Deterministic Processor**
- **Mode**: Rule-based, deterministic logic
- **Use Case**: Traditional trading strategies with clear rules
- **Features**:
  - Confidence threshold checks
  - Simple conflict resolution
  - Position size scaling by confidence
  - Risk management rules

#### **ML Light Processor**
- **Mode**: Light machine learning models
- **Use Case**: Strategies using ML for signal generation
- **Features**:
  - Feature extraction from signals
  - ML model prediction
  - Confidence scoring
  - Model-based position sizing

#### **LLM Reasoning Processor**
- **Mode**: Full LLM reasoning and decision making
- **Use Case**: Advanced strategies requiring complex reasoning
- **Features**:
  - Context-aware reasoning
  - Alternative scenario analysis
  - Risk assessment
  - Conservative position sizing

### 3. **Position Manager** (`tradeengine/position_manager.py`)
- **Purpose**: Tracks positions and enforces risk limits
- **Features**:
  - Real-time position tracking
  - P&L calculation (realized and unrealized)
  - Risk limit enforcement
  - Portfolio exposure monitoring
  - Position size limits

### 4. **Order Manager** (`tradeengine/order_manager.py`)
- **Purpose**: Manages order tracking and conditional execution
- **Features**:
  - Active order tracking
  - Conditional order monitoring
  - Order history management
  - Price monitoring for conditional orders
  - Order cancellation

### 5. **Enhanced Dispatcher** (`tradeengine/dispatcher.py`)
- **Purpose**: Orchestrates the entire trading process
- **Features**:
  - Signal aggregation integration
  - Advanced order execution
  - Risk management checks
  - Position and order tracking
  - Audit logging

## 🔄 Signal Flow

### 1. **Signal Reception**
```python
# Strategy sends signal
signal = Signal(
    strategy_id="momentum_v1",
    strategy_mode=StrategyMode.DETERMINISTIC,
    symbol="BTCUSDT",
    action="buy",
    confidence=0.85,
    current_price=45000.0,
    # ... other fields
)

# POST to /trade endpoint
response = await client.post("/trade", json=signal.dict())
```

### 2. **Signal Aggregation**
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

### 3. **Conflict Resolution**
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

### 4. **Order Execution**
```python
# Convert signal to order
trade_order = dispatcher._signal_to_order(signal, order_params)

# Execute with risk checks
if await dispatcher._check_risk_limits(trade_order):
    result = await dispatcher.execute_order(trade_order)

    # Update position tracking
    await position_manager.update_position(trade_order, result)

    # Track order
    await order_manager.track_order(trade_order, result)
```

## 🎛️ Strategy Modes

### **Deterministic Mode** (Default)
- **Description**: Rule-based, deterministic logic
- **Best For**: Traditional trading strategies
- **Features**:
  - Simple confidence thresholds
  - Basic conflict resolution
  - Position size scaling
  - Risk management rules

**Example Signal**:
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

### **ML Light Mode**
- **Description**: Light machine learning models
- **Best For**: ML-powered strategies
- **Features**:
  - Feature extraction
  - Model prediction
  - Confidence scoring
  - Model-based adjustments

**Example Signal**:
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

### **LLM Reasoning Mode**
- **Description**: Full LLM reasoning and decision making
- **Best For**: Complex strategies requiring reasoning
- **Features**:
  - Context-aware reasoning
  - Alternative analysis
  - Risk assessment
  - Conservative sizing

**Example Signal**:
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

## 🛡️ Risk Management

### **Position Limits**
- Maximum position size per symbol
- Portfolio exposure limits
- Daily loss limits
- Real-time risk monitoring

### **Signal Validation**
- Confidence thresholds
- Signal age limits
- Strategy weight validation
- Conflict resolution

### **Order Validation**
- Price validation
- Quantity validation
- Risk limit checks
- Exchange-specific validation

## 📊 Monitoring & Metrics

### **Prometheus Metrics**
- Trade execution latency
- Success/failure rates
- Position tracking
- Order status distribution

### **Health Checks**
- Component status monitoring
- Risk limit monitoring
- Order execution monitoring
- Position tracking monitoring

## 🔮 Future Enhancements

### **Phase 2: Advanced ML**
- Real ML model integration
- Feature engineering pipeline
- Model training and deployment
- A/B testing framework

### **Phase 3: LLM Integration**
- Real LLM API integration
- Context management
- Reasoning chain tracking
- Alternative scenario analysis

### **Phase 4: Advanced Features**
- Multi-exchange support
- Portfolio optimization
- Backtesting framework
- Real-time market data

## 🚀 Getting Started

### **1. Environment Setup**
```bash
# Install dependencies
make setup

# Configure environment
cp .env.example .env
# Edit .env with your settings
```

### **2. Start the Service**
```bash
# Run locally
make run

# Or with Docker
make run-docker
```

### **3. Send Test Signals**
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

## 📚 API Documentation

Visit `http://localhost:8000/docs` for interactive API documentation with examples for all endpoints.

## 🔒 Security Considerations

- API key authentication
- Rate limiting
- Input validation
- Audit logging
- Risk limit enforcement
- Secure configuration management

This architecture provides a robust foundation for multi-strategy trading with intelligent signal aggregation, risk management, and flexible execution modes.
