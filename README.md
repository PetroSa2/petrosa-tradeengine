# Petrosa Trading Engine MVP

**Phase 1 MVP** of a modular, event-driven trading execution system focused on crypto trading, starting with Binance.

## 🚀 Features

- **Comprehensive Order Types**: Market, limit, stop, stop-limit, take-profit, and take-profit-limit orders
- **Advanced Risk Management**: Automatic stop-loss and take-profit calculation
- **Live Trading**: Full Binance API integration with testnet and mainnet support
- **Simulation Mode**: Safe testing environment with realistic order simulation
- **Signal Processing**: REST API and NATS consumer for trading signals
- **Modular Architecture**: Clean separation of concerns with contracts, dispatcher, and exchange interfaces
- **Monitoring**: Prometheus metrics and MongoDB audit logging
- **Extensible**: Ready for future enhancements (risk management, multiple exchanges, AI tools)

## 🏗️ Architecture

```
petrosa/
├── contracts/         # Shared Pydantic models (Signal, TradeOrder)
├── tradeengine/       # Core engine (API, consumer, dispatcher, exchange)
│   ├── exchange/      # Exchange integrations (Binance, simulator)
│   ├── api.py         # FastAPI REST endpoints
│   ├── consumer.py    # NATS message consumer
│   └── dispatcher.py  # Signal to order conversion and execution
├── shared/            # Configuration, logging, utilities
│   ├── constants.py   # Centralized configuration and constants
│   ├── config.py      # Backward compatibility settings
│   └── logger.py      # Audit logging
└── examples/          # Usage examples and demonstrations
```

## 🔧 Tech Stack

- **Python 3.10+** with Poetry for dependency management
- **FastAPI** for REST API
- **Pydantic** for data validation and schemas
- **python-binance** for Binance API integration
- **MongoDB** (via motor) for audit logging
- **NATS** for event streaming
- **Prometheus** for metrics collection

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Poetry (installed automatically)
- MongoDB (optional - for audit logging)
- NATS (optional - for event consumption)
- Binance API keys (for live trading)

### Installation

```bash
# Install dependencies
make install

# Or manually with poetry
poetry install
```

### Configuration

1. **Copy environment template**:
   ```bash
   cp .env.example .env
   ```

2. **Set your Binance API keys** (for live trading):
   ```bash
   BINANCE_API_KEY=your_api_key
   BINANCE_API_SECRET=your_api_secret
   BINANCE_TESTNET=true  # Set to false for mainnet
   ```

3. **Configure trading parameters**:
   ```bash
   SIMULATION_ENABLED=true  # Set to false for live trading
   DEFAULT_BASE_AMOUNT=100.0
   STOP_LOSS_DEFAULT=2.0  # 2% default stop loss
   TAKE_PROFIT_DEFAULT=5.0  # 5% default take profit
   ```

### Running the API Server

```bash
# Start the FastAPI server
make run

# Server will be available at http://localhost:8000
# API docs at http://localhost:8000/docs
# Metrics at http://localhost:8000/metrics
```

### Running the NATS Consumer

```bash
# Start the signal consumer (requires NATS)
make consumer
```

### Testing Trading Functionality

```bash
# Run comprehensive trading examples
python examples/advanced_trading_example.py

# Run constants usage example
python examples/constants_usage.py
```

## 📊 API Endpoints

### Core Trading Endpoints

#### POST `/trade`

Process a trading signal and execute trade.

**Request Body:**
```json
{
  "strategy_id": "momentum_v1",
  "symbol": "BTCUSDT",
  "action": "buy",
  "price": 45000.0,
  "confidence": 0.85,
  "timestamp": "2025-06-29T12:00:00Z",
  "meta": {
    "order_type": "limit",
    "base_amount": 0.001,
    "stop_loss": 43000.0,
    "take_profit": 47000.0,
    "time_in_force": "GTC",
    "simulate": true,
    "indicators": {"rsi": 65, "macd": "bullish"},
    "rationale": "Strong momentum breakout"
  }
}
```

**Response:**
```json
{
  "message": "Signal processed successfully",
  "signal_id": "momentum_v1",
  "result": {
    "order_id": "12345678",
    "status": "filled",
    "side": "buy",
    "type": "limit",
    "amount": 0.001,
    "fill_price": 45000.0,
    "total_value": 45.0,
    "fees": 0.045,
    "simulated": true
  }
}
```

#### GET `/account`

Get account information from Binance.

**Response:**
```json
{
  "message": "Account information retrieved successfully",
  "data": {
    "can_trade": true,
    "maker_commission": 15,
    "taker_commission": 15,
    "balances": [...]
  }
}
```

#### GET `/price/{symbol}`

Get current price for a symbol.

**Response:**
```json
{
  "symbol": "BTCUSDT",
  "price": 45000.0,
  "timestamp": "2025-06-29T12:00:00Z"
}
```

#### DELETE `/order/{symbol}/{order_id}`

Cancel an existing order.

#### GET `/order/{symbol}/{order_id}`

Get status of an existing order.

### Utility Endpoints

- **GET `/`** - Health check endpoint
- **GET `/health`** - Detailed health check
- **GET `/version`** - Application version information
- **GET `/metrics`** - Prometheus metrics endpoint
- **GET `/docs`** - Interactive API documentation

## 🎯 Order Types Supported

### 1. Market Orders
- **Immediate execution** at current market price
- **Best for**: Quick entry/exit, high confidence signals
- **Risk**: Slippage in volatile markets

### 2. Limit Orders
- **Execution at specified price or better**
- **Best for**: Price-sensitive entries, reducing slippage
- **Risk**: May not fill if price doesn't reach target

### 3. Stop Orders
- **Market execution when price hits stop level**
- **Best for**: Stop losses, trend following
- **Risk**: Slippage during fast moves

### 4. Stop-Limit Orders
- **Limit execution when price hits stop level**
- **Best for**: Controlled stop losses, avoiding slippage
- **Risk**: May not fill if price gaps through limit

### 5. Take-Profit Orders
- **Market execution at profit target**
- **Best for**: Profit taking, trend following
- **Risk**: Slippage during fast moves

### 6. Take-Profit-Limit Orders
- **Limit execution at profit target**
- **Best for**: Controlled profit taking
- **Risk**: May not fill if price gaps through limit

## 🔒 Risk Management Features

### Automatic Stop-Loss Calculation
```json
{
  "meta": {
    "use_default_stop_loss": true,
    "stop_loss": 43000.0  // Override default
  }
}
```

### Automatic Take-Profit Calculation
```json
{
  "meta": {
    "use_default_take_profit": true,
    "take_profit": 47000.0  // Override default
  }
}
```

### Position Sizing
```json
{
  "meta": {
    "base_amount": 0.001,  // Base position size
    "confidence": 0.85     // Scales position by confidence
  }
}
```

## 🎮 Signal Meta Options

### Order Configuration
- `order_type`: "market", "limit", "stop", "stop_limit", "take_profit", "take_profit_limit"
- `base_amount`: Base position size
- `time_in_force`: "GTC", "IOC", "FOK"
- `quote_quantity`: Quote quantity for market orders

### Risk Management
- `stop_loss`: Custom stop loss price
- `take_profit`: Custom take profit price
- `use_default_stop_loss`: Use default percentage
- `use_default_take_profit`: Use default percentage

### Trading Mode
- `simulate`: Override global simulation setting
- `description`: Human-readable description
- `strategy_metadata`: Additional strategy information

## 🔧 Configuration

Configuration is handled via environment variables or `.env` file:

```bash
# Trading Configuration
SIMULATION_ENABLED=true
DEFAULT_BASE_AMOUNT=100.0
DEFAULT_ORDER_TYPE=market
STOP_LOSS_DEFAULT=2.0
TAKE_PROFIT_DEFAULT=5.0

# Binance Configuration
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=true

# Database
MONGODB_URL=mongodb://localhost:27017
MONGODB_DATABASE=petrosa

# NATS
NATS_SERVERS=nats://localhost:4222
NATS_SIGNAL_SUBJECT=signals.trading

# API
API_HOST=0.0.0.0
API_PORT=8000

# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO
```

## 📈 Monitoring

- **Prometheus Metrics**: Available at `/metrics`
  - `tradeengine_trades_total` - Total trades by status and type
  - `tradeengine_errors_total` - Total errors by type
  - `tradeengine_latency_seconds` - Trade execution latency
  - `tradeengine_nats_messages_processed_total` - NATS message processing

- **MongoDB Audit Log**: All trades are logged with full context including signal metadata

## 🔄 NATS Integration

Petrosa uses NATS for cloud-native event streaming, which is particularly well-suited for Kubernetes environments.

### NATS Setup

1. **Install NATS Server:**
   ```bash
   # macOS
   brew install nats-server

   # Docker
   docker run -p 4222:4222 nats:latest

   # Kubernetes
   helm repo add nats https://nats-io.github.io/k8s/helm/charts/
   helm install my-nats nats/nats
   ```

2. **Start NATS Server:**
   ```bash
   nats-server
   ```

3. **Test Signal Publishing:**
   ```bash
   python examples/publish_signal.py
   ```

## 🚦 Trade Flow

1. **Signal Input**:
   - Via REST API: `POST /trade`
   - Via NATS: Messages on `signals.trading` subject

2. **Signal Processing**:
   - Validate signal schema
   - Convert to TradeOrder via dispatcher
   - Apply risk management rules

3. **Trade Execution**:
   - Simulated: Execute via built-in simulator
   - Live: Route to Binance API client

4. **Order Management**:
   - Monitor order status
   - Cancel orders if needed
   - Track fills and fees

5. **Logging & Metrics**:
   - Audit log to MongoDB
   - Update Prometheus metrics

## 🛡️ Safety Features

### Simulation Mode
- **Default**: All orders are simulated
- **No real money at risk**
- **Perfect for strategy testing**

### Testnet Support
- **Binance Testnet**: Safe testing environment
- **Real API responses**: Accurate simulation
- **No real money involved**

### Validation
- **Signal validation**: Confidence, action, price ranges
- **Order validation**: Symbol support, price precision
- **Risk checks**: Position size limits, balance checks

## 🔮 Future Enhancements

- **Risk Management**: Position sizing, exposure limits, drawdown protection
- **Multi-Exchange**: Coinbase, Kraken support
- **Portfolio Management**: Position tracking, P&L calculation
- **Advanced Orders**: OCO orders, trailing stops
- **Market Data**: Real-time price feeds, order book data
- **Backtesting**: Historical strategy testing
- **AI Integration**: Machine learning signal generation

## 📚 Examples

### Basic Market Order
```python
signal = Signal(
    strategy_id="simple_strategy",
    symbol="BTCUSDT",
    action="buy",
    price=45000.0,
    confidence=0.8,
    timestamp=datetime.now(),
    meta={"order_type": "market", "base_amount": 0.001}
)
```

### Advanced Limit Order with Risk Management
```python
signal = Signal(
    strategy_id="advanced_strategy",
    symbol="ETHUSDT",
    action="buy",
    price=3000.0,
    confidence=0.9,
    timestamp=datetime.now(),
    meta={
        "order_type": "limit",
        "base_amount": 0.1,
        "stop_loss": 2850.0,
        "take_profit": 3300.0,
        "time_in_force": "GTC"
    }
)
```

### Stop Loss Order
```python
signal = Signal(
    strategy_id="risk_management",
    symbol="BTCUSDT",
    action="sell",
    price=45000.0,
    confidence=0.95,
    timestamp=datetime.now(),
    meta={
        "order_type": "stop",
        "base_amount": 0.001,
        "stop_loss": 43000.0
    }
)
```

## 🚨 Important Notes

1. **Start with Simulation**: Always test in simulation mode first
2. **Use Testnet**: Use Binance testnet for live testing
3. **Small Amounts**: Start with small position sizes
4. **Monitor Orders**: Always check order status and fills
5. **Risk Management**: Always use stop losses and position sizing
6. **API Limits**: Respect Binance API rate limits

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
# Trigger CI/CD Pipeline

## Pre-commit Enforcement

This repository enforces code quality and formatting on every commit using [pre-commit](https://pre-commit.com/). **No commit can be made unless all pre-commit hooks pass.**

### How It Works
- **Automatic Installation:**
  - `make setup` and `make install-dev` will automatically install `pre-commit` (if missing) and set up the hooks for you.
- **Mandatory Local Checks:**
  - A custom `.git/hooks/pre-commit` script blocks all commits if `pre-commit` is not installed, and runs all hooks before every commit.
  - If any hook fails (black, ruff, mypy, isort, etc.), the commit is blocked until you fix the issues.
- **No Bypassing:**
  - You cannot commit unless pre-commit is installed and all checks pass.
  - If you try to commit without pre-commit, you will see an error message with installation instructions.

### What Developers Should Do
1. Run `make setup` or `make install-dev` after cloning the repo.
2. Commit as usual. If a hook fails, fix the issues and try again.
3. If you see an error about pre-commit not being installed, run:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

### Hooks Enforced
- **black** (code formatting)
- **ruff** (linting)
- **mypy** (type checking)
- **isort** (import sorting)
- **bandit** (security, if enabled)
- **YAML/JSON/TOML checks**
- **trailing whitespace, end-of-file, and more**

See `.pre-commit-config.yaml` for the full list.

## Running Tests Locally

To run tests locally, you must set the required MongoDB environment variables. You can do this by exporting them in your shell:

```sh
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DATABASE="test"
make pipeline
```

Or, create a `.env.test` file in the project root with the following contents:

```
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=test
BINANCE_API_KEY=test
BINANCE_API_SECRET=test
JWT_SECRET_KEY=test
```

If your test runner loads `.env.test` automatically, these values will be used for local testing. This ensures the catastrophic failure logic is satisfied and tests will run successfully.
