# Petrosa Trading Engine

**Production-ready cryptocurrency trading engine** with dynamic minimum order amounts, comprehensive CI/CD pipeline, and Kubernetes deployment.

## 🚀 Features

- **Dynamic Minimum Order Amounts**: Automatically calculates minimum order requirements for each symbol
- **Comprehensive Order Types**: Market, limit, stop, stop-limit, take-profit, and take-profit-limit orders
- **Advanced Risk Management**: Automatic stop-loss and take-profit calculation
- **Live Trading**: Full Binance API integration with testnet and mainnet support
- **Simulation Mode**: Safe testing environment with realistic order simulation
- **Signal Processing**: REST API and NATS consumer for trading signals
- **Modular Architecture**: Clean separation of concerns with contracts, dispatcher, and exchange interfaces
- **Monitoring**: Prometheus metrics and MongoDB audit logging
- **Kubernetes Ready**: Complete CI/CD pipeline with auto-incremented versioning
- **Production Deployment**: Remote MicroK8s cluster with SSL ingress

## 🏗️ Architecture

```
petrosa-tradeengine/
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
├── k8s/               # Kubernetes manifests for production deployment
├── scripts/           # Automation and testing scripts
└── examples/          # Usage examples and demonstrations
```

## 🔧 Tech Stack

- **Python 3.11+** with Poetry for dependency management
- **FastAPI** for REST API
- **Pydantic** for data validation and schemas
- **python-binance** for Binance API integration
- **MongoDB** (via motor) for audit logging
- **NATS** for event streaming
- **Prometheus** for metrics collection
- **Kubernetes** for production deployment
- **Docker** for containerization
- **GitHub Actions** for CI/CD pipeline

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker (for containerization)
- kubectl (for Kubernetes deployment)
- Make (for automation)

### Installation

```bash
# Complete setup with all dependencies
make setup

# Install development dependencies
make install-dev

# Run local pipeline (lint, test, build)
make pipeline
```

### Configuration

1. **Environment Variables**:
   ```bash
   # Binance Configuration
   BINANCE_API_KEY=your_api_key
   BINANCE_API_SECRET=your_api_secret
   BINANCE_TESTNET=true  # Set to false for mainnet

   # Trading Configuration
   SIMULATION_ENABLED=true  # Set to false for live trading
   ENVIRONMENT=production
   LOG_LEVEL=INFO

   # Database
   MONGODB_URL=mongodb://localhost:27017
   MONGODB_DATABASE=petrosa

   # JWT
   JWT_SECRET_KEY=your_jwt_secret
   ```

### Running Locally

```bash
# Start the FastAPI server
make run

# Server will be available at http://localhost:8000
# API docs at http://localhost:8000/docs
# Metrics at http://localhost:8000/metrics
```

### Running in Docker

```bash
# Build Docker image
make build

# Run in Docker
make run-docker

# Test container
make container
```

## 🚀 Production Deployment

### Kubernetes Deployment

The project includes a complete CI/CD pipeline that automatically:

1. **Creates versioned releases** (v1.1.21, v1.1.22, etc.)
2. **Builds and pushes Docker images** to Docker Hub
3. **Deploys to Kubernetes** with proper health checks
4. **Updates with zero downtime** using rolling deployments

### Deployment Commands

```bash
# Deploy to Kubernetes
make deploy

# Check deployment status
make k8s-status

# View logs
make k8s-logs

# Monitor deployment
make monitor
```

### Kubernetes Resources

- **Namespace**: `petrosa-apps`
- **Deployment**: 3 replicas with health checks
- **Service**: ClusterIP on port 80
- **Ingress**: SSL-enabled with Let's Encrypt
- **HPA**: Auto-scaling based on CPU/memory

## 📊 API Endpoints

### Core Trading Endpoints

#### POST `/trade/signal`

Process a trading signal with dynamic minimum amounts.

**Request Body:**
```json
{
  "strategy_id": "momentum_v1",
  "symbol": "BTCUSDT",
  "action": "buy",
  "price": 45000.0,
  "quantity": 0.0,  // Will use dynamic minimum if zero
  "confidence": 0.85,
  "timestamp": "2025-06-29T12:00:00Z",
  "meta": {
    "order_type": "market",
    "stop_loss": 43000.0,
    "take_profit": 47000.0,
    "time_in_force": "GTC"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "signal": { ... },
  "result": {
    "status": "executed",
    "order_params": {
      "symbol": "BTCUSDT",
      "side": "buy",
      "type": "market",
      "time_in_force": "GTC"
    },
    "confidence": 0.85,
    "reason": "Deterministic rules satisfied"
  }
}
```

#### GET `/account`

Get account information from Binance.

#### GET `/price/{symbol}`

Get current price for a symbol.

#### DELETE `/order/{symbol}/{order_id}`

Cancel an existing order.

#### GET `/order/{symbol}/{order_id}`

Get status of an existing order.

### Health Endpoints

- **GET `/health`** - Detailed health status
- **GET `/ready`** - Readiness probe
- **GET `/live`** - Liveness probe
- **GET `/metrics`** - Prometheus metrics

## 🎯 Dynamic Minimum Order Amounts

### The Problem
Each cryptocurrency symbol has different minimum order requirements:
- **BTCUSDT**: 0.00001 BTC minimum
- **DOGEUSDT**: 100 DOGE minimum
- **ETHUSDT**: 0.001 ETH minimum

### The Solution
The system automatically calculates the minimum order amount for each symbol:

```python
def calculate_min_order_amount(self, symbol: str, current_price: float = None) -> float:
    """Calculate minimum order amount that meets all requirements"""
    min_info = self.get_min_order_amount(symbol)
    min_qty = float(min_info["min_qty"])
    min_notional = float(min_info["min_notional"])

    if current_price:
        # Calculate minimum quantity based on price
        min_qty_by_price = min_notional / current_price
        return max(min_qty, min_qty_by_price)

    return min_qty
```

### Usage
- **Zero quantity**: System uses calculated minimum
- **Valid quantity**: System uses provided quantity
- **Invalid quantity**: System uses calculated minimum

## 🔒 Risk Management Features

### Automatic Stop-Loss Calculation
```json
{
  "stop_loss": 43000.0,  // Custom stop loss
  "stop_loss_pct": 2.0   // Or percentage-based
}
```

### Automatic Take-Profit Calculation
```json
{
  "take_profit": 47000.0,  // Custom take profit
  "take_profit_pct": 5.0   // Or percentage-based
}
```

### Position Sizing
```json
{
  "quantity": 0.001,     // Fixed quantity
  "position_size_pct": 5.0  // Or percentage of balance
}
```

## 🎮 Order Types Supported

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

## 🔧 Configuration

### Environment Variables

```bash
# Trading Configuration
SIMULATION_ENABLED=true
ENVIRONMENT=production
LOG_LEVEL=INFO

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

# JWT
JWT_SECRET_KEY=your_jwt_secret
```

## 📈 Monitoring

### Prometheus Metrics
Available at `/metrics`:
- `tradeengine_trades_total` - Total trades by status and type
- `tradeengine_errors_total` - Total errors by type
- `tradeengine_latency_seconds` - Trade execution latency
- `tradeengine_nats_messages_processed_total` - NATS message processing

### Health Checks
- **Readiness Probe**: `/ready` - Service is ready to receive traffic
- **Liveness Probe**: `/live` - Service is alive and responding
- **Health Check**: `/health` - Detailed health status

### Audit Logging
All trades are logged to MongoDB with full context including signal metadata.

## 🚦 Trade Flow

1. **Signal Input**:
   - Via REST API: `POST /trade/signal`
   - Via NATS: Messages on `signals.trading` subject

2. **Signal Processing**:
   - Validate signal schema
   - Convert to TradeOrder via dispatcher
   - Calculate dynamic minimum amounts
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
- **Dynamic minimums**: Symbol-specific requirements

## 🔄 CI/CD Pipeline

### Automated Workflow
1. **Code Push** → Triggers GitHub Actions
2. **Lint & Test** → Code quality checks
3. **Security Scan** → Vulnerability scanning
4. **Create Release** → Auto-increment version (v1.1.21, v1.1.22, etc.)
5. **Build & Push** → Docker image to Docker Hub
6. **Deploy** → Kubernetes deployment
7. **Verify** → Health checks and monitoring

### Version Management
- **Auto-incremented versions**: v1.1.21, v1.1.22, etc.
- **Semantic versioning**: Major.Minor.Patch
- **Git tags**: Automatic tag creation
- **Docker tags**: Versioned and latest tags

## 📚 Examples

### Basic Market Order with Dynamic Minimums
```python
signal = Signal(
    strategy_id="simple_strategy",
    symbol="BTCUSDT",
    action="buy",
    price=45000.0,
    quantity=0.0,  # Will use dynamic minimum
    confidence=0.8,
    timestamp=datetime.now(),
    meta={"order_type": "market"}
)
```

### Advanced Limit Order with Risk Management
```python
signal = Signal(
    strategy_id="advanced_strategy",
    symbol="ETHUSDT",
    action="buy",
    price=3000.0,
    quantity=0.005,  # Valid quantity, will be used
    confidence=0.9,
    timestamp=datetime.now(),
    meta={
        "order_type": "limit",
        "stop_loss": 2850.0,
        "take_profit": 3300.0,
        "time_in_force": "GTC"
    }
}
```

### Testing Different Symbols
```python
# BTCUSDT - will use ~0.00001 BTC minimum
signal_btc = Signal(symbol="BTCUSDT", quantity=0.0, ...)

# DOGEUSDT - will use ~100 DOGE minimum
signal_doge = Signal(symbol="DOGEUSDT", quantity=0.0, ...)

# ETHUSDT - will use ~0.001 ETH minimum
signal_eth = Signal(symbol="ETHUSDT", quantity=0.0, ...)
```

## 🚨 Important Notes

1. **Start with Simulation**: Always test in simulation mode first
2. **Use Testnet**: Use Binance testnet for live testing
3. **Dynamic Minimums**: System automatically handles symbol-specific requirements
4. **Monitor Orders**: Always check order status and fills
5. **Risk Management**: Always use stop losses and position sizing
6. **API Limits**: Respect Binance API rate limits
7. **Production Ready**: Full CI/CD pipeline with Kubernetes deployment

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
