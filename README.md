# Petrosa Trading Engine MVP

**Phase 1 MVP** of a modular, event-driven trading execution system focused on crypto trading, starting with Binance.

## 🚀 Features

- **Signal Processing**: REST API and NATS consumer for trading signals
- **Modular Architecture**: Clean separation of concerns with contracts, dispatcher, and exchange interfaces
- **Simulation Support**: Built-in trade simulator for testing and development
- **Monitoring**: Prometheus metrics and MongoDB audit logging
- **Extensible**: Ready for future enhancements (risk management, multiple exchanges, AI tools)

## 🏗️ Architecture

```
petrosa/
├── contracts/         # Shared Pydantic models (Signal, TradeOrder)
├── tradeengine/       # Core engine (API, consumer, dispatcher, exchange)
├── shared/            # Configuration, logging, utilities
└── infra/             # Infrastructure (future: Docker, monitoring)
```

## 🔧 Tech Stack

- **Python 3.10+** with Poetry for dependency management
- **FastAPI** for REST API
- **Pydantic** for data validation and schemas
- **MongoDB** (via motor) for audit logging
- **NATS** for event streaming
- **Prometheus** for metrics collection

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Poetry (installed automatically)
- MongoDB (optional - for audit logging)
- NATS (optional - for event consumption)

### Installation

```bash
# Install dependencies
make install

# Or manually with poetry
poetry install
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

## 📊 API Endpoints

### GET `/`

Health check endpoint returning basic service information.

### GET `/health`

Detailed health check endpoint.

### GET `/version`

Get detailed version information including build date and API version.

**Response:**
```json
{
  "name": "Petrosa Trading Engine",
  "version": "0.1.0",
  "description": "Petrosa Trading Engine MVP - Signal-driven trading execution",
  "build_date": "2025-06-29",
  "python_version": "3.10+",
  "api_version": "v1"
}
```

### GET `/openapi.json`

Get the complete OpenAPI specification in JSON format for API documentation and client generation.

### GET `/docs`

Automatic interactive API documentation (Swagger UI) - built-in FastAPI feature.

### GET `/metrics`

Prometheus metrics endpoint.

### POST `/trade`

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
    "order_id": "uuid-here",
    "status": "filled",
    "side": "buy",
    "amount": 85.0,
    "fill_price": 45045.0,
    "simulated": true
  }
}
```

### GET `/health`

Health check endpoint.

### GET `/metrics`

Prometheus metrics endpoint.

## 🔧 Configuration

Configuration is handled via environment variables or `.env` file:

```bash
# MongoDB
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

### NATS vs Kafka Benefits

- **Lightweight**: Smaller footprint and simpler deployment
- **Cloud Native**: Designed for Kubernetes and microservices
- **High Performance**: Sub-millisecond latency
- **Built-in Load Balancing**: Queue groups for consumer scaling
- **Request-Reply**: Bi-directional communication support


```bash
# Format code
make format

# Lint code
make lint

# Run tests
make test

# Clean cache files
make clean
```

## 🚦 Trade Flow

1. **Signal Input**: 
   - Via REST API: `POST /trade`
   - Via NATS: Messages on `signals.trading` subject

2. **Signal Processing**:
   - Validate signal schema
   - Convert to TradeOrder via dispatcher

3. **Trade Execution**:
   - Simulated: Execute via built-in simulator
   - Live: Route to exchange client (future)

4. **Logging & Metrics**:
   - Audit log to MongoDB
   - Update Prometheus metrics

## 🔮 Future Enhancements

- **Risk Management**: Position sizing, exposure limits, drawdown protection
- **Multi-Exchange**: Binance, Coinbase, Kraken support
- **Live Trading**: Real exchange integration
- **Dashboard**: Web UI for monitoring and control
- **AI Tools**: Strategy optimization, market analysis
- **Advanced Orders**: Complex order types, algorithmic execution

## 📝 Example Usage

### Send a trading signal via API:

```bash
curl -X POST "http://localhost:8000/trade" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "test_strategy",
    "symbol": "BTCUSDT", 
    "action": "buy",
    "price": 45000.0,
    "confidence": 0.8,
    "timestamp": "2025-06-29T12:00:00Z",
    "meta": {"simulate": true}
  }'
```

### Check system health:

```bash
curl http://localhost:8000/health
```

### Get version information:

```bash
curl http://localhost:8000/version
```

### Get OpenAPI specs:

```bash
curl http://localhost:8000/openapi.json
```

### View interactive API docs:

Open `http://localhost:8000/docs` in your browser for Swagger UI.

### View metrics:

```bash
curl http://localhost:8000/metrics
```

## 📄 License

Copyright (c) 2025 Petrosa Team. All rights reserved.
