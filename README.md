# Petrosa Trading Engine

**Production-ready cryptocurrency order execution system with advanced risk management and Binance integration**

A modular, event-driven trading execution system focused on crypto trading. Consumes trading signals from NATS, applies risk management rules, and executes orders on Binance with comprehensive audit logging. Supports both simulation and live trading modes.

---

## 🌐 PETROSA ECOSYSTEM OVERVIEW

[Same ecosystem overview as other services - maintaining consistency]

### Services in the Ecosystem

| Service | Purpose | Input | Output | Status |
|---------|---------|-------|--------|--------|
| **petrosa-socket-client** | Real-time WebSocket data ingestion | Binance WebSocket API | NATS: `binance.websocket.data` | Real-time Processing |
| **petrosa-binance-data-extractor** | Historical data extraction & gap filling | Binance REST API | MySQL (klines, funding rates, trades) | Batch Processing |
| **petrosa-bot-ta-analysis** | Technical analysis (28 strategies) | Data Manager API | NATS: `signals.trading` | Signal Generation |
| **petrosa-realtime-strategies** | Real-time signal generation | NATS: `binance.websocket.data` | NATS: `signals.trading` | Live Processing |
| **petrosa-tradeengine** | Order execution & trade management | NATS: `signals.trading` | Binance Orders API, MongoDB audit | **YOU ARE HERE** |
| **petrosa_k8s** | Centralized infrastructure | Kubernetes manifests | Cluster resources | Infrastructure |

### Data Flow Pipeline

```
┌──────────────────┐
│   TA Bot         │
│ (28 Strategies)  │
└────┬─────────────┘
     │ NATS: signals.trading
     │
     ├────────────────────────────┐
     │                            │
     ▼                            ▼
┌──────────────────┐    ┌────────────────────┐
│  Realtime        │    │  Trade Engine      │ ◄── THIS SERVICE
│  Strategies      │───▶│  (Order Execution) │
│  (Live Signals)  │    │                    │
└──────────────────┘    │  • Consume signals │
                         │  • Validate        │
                         │  • Risk management │
                         │  • Execute orders  │
                         │  • Audit logging   │
                         └────┬───────────────┘
                              │
                              ├─────────────────┐
                              │                 │
                              ▼                 ▼
                         ┌──────────────┐  ┌──────────────┐
                         │  Binance API │  │   MongoDB    │
                         │  (Orders)    │  │  (Audit Log) │
                         └──────────────┘  └──────────────┘
```

### Transport Layer

#### NATS Messaging (Input)

**Consumed Topic:** `signals.trading`

**Signal Message Format:**
```json
{
  "strategy_id": "volume_surge_breakout",
  "symbol": "BTCUSDT",
  "action": "buy",
  "confidence": 0.85,
  "price": 50000.00,
  "quantity": 0.001,
  "current_price": 50000.00,
  "timeframe": "15m",
  "stop_loss": 49000.00,
  "take_profit": 51500.00,
  "indicators": {...},
  "metadata": {...},
  "timestamp": "2024-01-01T00:00:00.000Z"
}
```

#### Binance API (Output)

**Endpoints Used:**

| Endpoint | Purpose | Order Type |
|----------|---------|------------|
| `POST /api/v3/order` | Place new order | Market, Limit |
| `POST /fapi/v1/order` | Place futures order | All futures types |
| `DELETE /api/v3/order` | Cancel order | Any |
| `GET /api/v3/order` | Query order status | Any |
| `GET /api/v3/account` | Get account info | N/A |

**Order Request Format (Limit Order):**
```json
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "type": "LIMIT",
  "timeInForce": "GTC",
  "quantity": "0.001",
  "price": "50000.00",
  "newClientOrderId": "unique-order-id"
}
```

#### MongoDB (Audit Log)

**Collection:** `trade_audit_log`

**Document Structure:**
```json
{
  "_id": "ObjectId",
  "signal_id": "volume_surge_breakout",
  "symbol": "BTCUSDT",
  "side": "buy",
  "order_type": "limit",
  "quantity": 0.001,
  "price": 50000.00,
  "stop_loss": 49000.00,
  "take_profit": 51500.00,
  "binance_order_id": "12345678",
  "status": "filled",
  "fill_price": 50005.00,
  "fees": 0.00005,
  "timestamp": "2024-01-01T00:00:00.000Z",
  "simulation_mode": false,
  "metadata": {...}
}
```

---

## 🔧 TRADE ENGINE - DETAILED DOCUMENTATION

### Service Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                   Trade Engine Architecture                         │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │                    Main Service                           │     │
│  │                                                            │     │
│  │  • FastAPI REST Server (HTTP:8000)                       │     │
│  │  • NATS Consumer (signals.trading)                       │     │
│  │  • Signal Dispatcher                                     │     │
│  │  • Exchange Interface (Binance)                          │     │
│  │  • Audit Logger (MongoDB)                                │     │
│  └──────┬───────────────────────────────────────────────────┘     │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │            Signal Dispatcher                              │     │
│  │                                                            │     │
│  │  1. Validate signal (completeness, confidence >= 0.5)    │     │
│  │  2. Convert signal → TradeOrder                          │     │
│  │  3. Apply risk management rules                          │     │
│  │  4. Calculate position sizing                            │     │
│  │  5. Set stop loss / take profit                          │     │
│  │  6. Route to exchange                                    │     │
│  └──────┬───────────────────────────────────────────────────┘     │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │              Risk Management                              │     │
│  │                                                            │     │
│  │  • Position Size Calculation                             │     │
│  │    - Based on confidence score                           │     │
│  │    - Account balance check                               │     │
│  │    - Maximum position limit                              │     │
│  │                                                            │     │
│  │  • Stop Loss Calculation                                 │     │
│  │    - Default: 2% from entry                              │     │
│  │    - ATR-based (if available)                            │     │
│  │    - Custom from signal                                  │     │
│  │                                                            │     │
│  │  • Take Profit Calculation                               │     │
│  │    - Default: 5% from entry (2.5:1 R:R)                 │     │
│  │    - ATR-based (if available)                            │     │
│  │    - Custom from signal                                  │     │
│  │                                                            │     │
│  │  • Pre-Trade Validation                                  │     │
│  │    - Symbol support check                                │     │
│  │    - Minimum notional value (Binance: $10)              │     │
│  │    - Balance sufficiency                                 │     │
│  │    - Max open positions limit                            │     │
│  └──────┬───────────────────────────────────────────────────┘     │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │            Exchange Interface (Binance)                   │     │
│  │                                                            │     │
│  │  Supported Order Types:                                  │     │
│  │  • Market      - Immediate execution                     │     │
│  │  • Limit       - Price-specified                         │     │
│  │  • Stop        - Stop loss (market)                      │     │
│  │  • Stop Limit  - Stop loss (limit)                       │     │
│  │  • Take Profit - Take profit (market)                    │     │
│  │  • TP Limit    - Take profit (limit)                     │     │
│  │                                                            │     │
│  │  Features:                                               │     │
│  │  • API rate limiting (1200 req/min)                     │     │
│  │  • Retry with exponential backoff                        │     │
│  │  • Order status tracking                                 │     │
│  │  • Fill price capture                                    │     │
│  │  • Fee calculation                                       │     │
│  └──────┬───────────────────────────────────────────────────┘     │
│         │                                                            │
│         ├────────────────────┬───────────────────────────┐        │
│         │                    │                           │        │
│         ▼                    ▼                           ▼        │
│  ┌────────────┐    ┌─────────────────┐    ┌──────────────────┐  │
│  │ Simulation │    │  Binance Testnet│    │  Binance Mainnet │  │
│  │   Mode     │    │  (Safe Testing) │    │  (Live Trading)  │  │
│  │            │    │                 │    │                  │  │
│  │ • No real  │    │ • Test API      │    │ • Real money     │  │
│  │   API calls│    │ • Fake orders   │    │ • Real orders    │  │
│  │ • Instant  │    │ • No real money │    │ • Full execution │  │
│  │   fills    │    │                 │    │                  │  │
│  └────────────┘    └─────────────────┘    └──────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │            MongoDB Audit Logger                           │     │
│  │                                                            │     │
│  │  Log Every Trade:                                        │     │
│  │  • Signal received                                       │     │
│  │  • Order placed                                          │     │
│  │  • Order filled                                          │     │
│  │  • Order cancelled                                       │     │
│  │  • Errors encountered                                    │     │
│  │                                                            │     │
│  │  Full Context:                                           │     │
│  │  • Signal metadata                                       │     │
│  │  • Risk management parameters                            │     │
│  │  • Binance response                                      │     │
│  │  • Execution timestamps                                  │     │
│  └──────────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────────┘
```

### Core Components

#### 1. Signal Dispatcher (`tradeengine/dispatcher.py`)

**Signal to Order Conversion:**

```python
class SignalDispatcher:
    """
    Convert trading signals to executable orders with risk management.
    """

    def __init__(
        self,
        exchange: BinanceExchange,
        mongo_logger: MongoAuditLogger,
        default_stop_loss_pct: float = 2.0,
        default_take_profit_pct: float = 5.0
    ):
        self.exchange = exchange
        self.mongo_logger = mongo_logger
        self.default_stop_loss_pct = default_stop_loss_pct
        self.default_take_profit_pct = default_take_profit_pct

    async def process_signal(self, signal: Signal) -> TradeResult:
        """
        Process signal and execute trade.

        Pipeline:
        1. Validate signal
        2. Calculate position size
        3. Calculate risk levels
        4. Create trade order
        5. Execute on exchange
        6. Log to MongoDB

        Args:
            signal: Trading signal from strategy

        Returns:
            TradeResult with execution details
        """
        # Validate signal
        if not self._validate_signal(signal):
            return TradeResult(
                success=False,
                message="Signal validation failed"
            )

        # Calculate position size
        quantity = self._calculate_position_size(
            signal.symbol,
            signal.confidence,
            signal.current_price
        )

        # Calculate risk levels
        stop_loss, take_profit = self._calculate_risk_levels(
            signal.action,
            signal.current_price,
            signal.stop_loss,
            signal.take_profit
        )

        # Create trade order
        order = TradeOrder(
            symbol=signal.symbol,
            side=signal.action,
            type="limit",
            amount=quantity,
            target_price=signal.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_metadata={
                "strategy_id": signal.strategy_id,
                "confidence": signal.confidence,
                "timeframe": signal.timeframe
            }
        )

        # Execute trade
        try:
            result = await self.exchange.place_order(order)

            # Log to MongoDB
            await self.mongo_logger.log_trade(
                signal=signal,
                order=order,
                result=result
            )

            return result

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            await self.mongo_logger.log_error(signal, str(e))
            return TradeResult(success=False, message=str(e))

    def _calculate_position_size(
        self,
        symbol: str,
        confidence: float,
        price: float
    ) -> float:
        """
        Calculate position size based on confidence and account balance.

        Formula:
        base_size = account_balance * 0.02  # 2% per trade
        adjusted_size = base_size * confidence
        quantity = adjusted_size / price
        """
        # Get account balance
        account = self.exchange.get_account()
        usdt_balance = float(account.get("USDT", {}).get("free", 0))

        # Calculate base size (2% of account)
        base_size_usdt = usdt_balance * 0.02

        # Adjust by confidence
        position_size_usdt = base_size_usdt * confidence

        # Convert to quantity
        quantity = position_size_usdt / price

        # Apply limits
        min_quantity = self.exchange.get_min_quantity(symbol)
        max_quantity = self.exchange.get_max_quantity(symbol)

        quantity = max(min_quantity, min(quantity, max_quantity))

        return quantity

    def _calculate_risk_levels(
        self,
        action: str,
        entry_price: float,
        signal_stop_loss: Optional[float],
        signal_take_profit: Optional[float]
    ) -> Tuple[float, float]:
        """Calculate stop loss and take profit levels."""
        # Use signal levels if provided, otherwise calculate
        if action == "buy":
            stop_loss = signal_stop_loss or entry_price * (1 - self.default_stop_loss_pct / 100)
            take_profit = signal_take_profit or entry_price * (1 + self.default_take_profit_pct / 100)
        else:  # sell
            stop_loss = signal_stop_loss or entry_price * (1 + self.default_stop_loss_pct / 100)
            take_profit = signal_take_profit or entry_price * (1 - self.default_take_profit_pct / 100)

        return stop_loss, take_profit
```

#### 2. Binance Exchange (`tradeengine/exchange/binance.py`)

**Order Execution:**

```python
class BinanceExchange:
    """Binance exchange implementation."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        simulation: bool = True
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.simulation = simulation

        if simulation:
            self.client = BinanceSimulator()
        else:
            self.client = Client(api_key, api_secret, testnet=testnet)

    async def place_order(self, order: TradeOrder) -> TradeResult:
        """
        Place order on Binance.

        Supports:
        - Market orders (immediate execution)
        - Limit orders (price-specified)
        - Stop loss orders
        - Take profit orders
        """
        if self.simulation:
            return await self._simulate_order(order)

        try:
            # Prepare order parameters
            params = {
                "symbol": order.symbol,
                "side": order.side.upper(),
                "type": order.type.upper(),
                "quantity": order.amount
            }

            # Add price for limit orders
            if order.type == "limit":
                params["price"] = order.target_price
                params["timeInForce"] = "GTC"

            # Place primary order
            response = self.client.create_order(**params)

            # Place stop loss (if specified)
            if order.stop_loss:
                await self._place_stop_loss(
                    symbol=order.symbol,
                    side="SELL" if order.side == "BUY" else "BUY",
                    quantity=order.amount,
                    stop_price=order.stop_loss
                )

            # Place take profit (if specified)
            if order.take_profit:
                await self._place_take_profit(
                    symbol=order.symbol,
                    side="SELL" if order.side == "BUY" else "BUY",
                    quantity=order.amount,
                    price=order.take_profit
                )

            # Parse result
            return TradeResult(
                success=True,
                order_id=response["orderId"],
                status=response["status"],
                fill_price=float(response.get("price", 0)),
                quantity=float(response["executedQty"]),
                fees=self._calculate_fees(response)
            )

        except BinanceAPIException as e:
            logger.error(f"Binance API error: {e}")
            return TradeResult(
                success=False,
                message=f"API Error: {e.message}"
            )

    async def _simulate_order(self, order: TradeOrder) -> TradeResult:
        """Simulate order execution (no real API call)."""
        return TradeResult(
            success=True,
            order_id=f"SIM-{uuid.uuid4()}",
            status="filled",
            fill_price=order.target_price or order.amount,
            quantity=order.amount,
            fees=order.amount * 0.001,  # 0.1% fee
            simulated=True
        )

    def _calculate_fees(self, response: dict) -> float:
        """Calculate trading fees from response."""
        fills = response.get("fills", [])
        total_fees = sum(float(fill.get("commission", 0)) for fill in fills)
        return total_fees
```

#### 3. FastAPI REST Server (`tradeengine/api.py`)

**REST Endpoints:**

```python
from fastapi import FastAPI, HTTPException
from contracts.signal import Signal
from contracts.order import TradeOrder

app = FastAPI(title="Petrosa Trade Engine")

@app.post("/trade")
async def process_trade_signal(signal: Signal):
    """
    Process a trading signal and execute trade.

    Request Body:
    {
      "strategy_id": "volume_surge_breakout",
      "symbol": "BTCUSDT",
      "action": "buy",
      "confidence": 0.85,
      "price": 50000.00,
      "quantity": 0.001,
      "stop_loss": 49000.00,
      "take_profit": 51500.00
    }

    Response:
    {
      "message": "Signal processed successfully",
      "result": {
        "order_id": "12345678",
        "status": "filled",
        "fill_price": 50005.00,
        "quantity": 0.001,
        "fees": 0.00005
      }
    }
    """
    try:
        result = await dispatcher.process_signal(signal)

        if result.success:
            return {
                "message": "Signal processed successfully",
                "signal_id": signal.strategy_id,
                "result": result.dict()
            }
        else:
            raise HTTPException(status_code=400, detail=result.message)

    except Exception as e:
        logger.error(f"Trade processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/account")
async def get_account_info():
    """Get Binance account information."""
    try:
        account = exchange.get_account()
        return {
            "message": "Account information retrieved",
            "data": account
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/price/{symbol}")
async def get_price(symbol: str):
    """Get current price for symbol."""
    try:
        price = exchange.get_price(symbol)
        return {
            "symbol": symbol,
            "price": price,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/order/{symbol}/{order_id}")
async def cancel_order(symbol: str, order_id: str):
    """Cancel an existing order."""
    try:
        result = exchange.cancel_order(symbol, order_id)
        return {
            "message": "Order cancelled",
            "order_id": order_id,
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics")
async def get_metrics():
    """Get Prometheus metrics."""
    return {
        "trades_total": metrics.trades_total,
        "trades_success": metrics.trades_success,
        "trades_failed": metrics.trades_failed,
        "total_volume_usdt": metrics.total_volume_usdt
    }
```

### Supported Order Types

| Order Type | Description | Best For | Risk |
|------------|-------------|----------|------|
| **Market** | Immediate execution at current price | Quick entry/exit, high confidence | Slippage in volatile markets |
| **Limit** | Execution at specified price or better | Price-sensitive entries | May not fill if price doesn't reach |
| **Stop** | Market order when price hits stop level | Stop losses, trend following | Slippage during fast moves |
| **Stop Limit** | Limit order when price hits stop level | Controlled stop losses | May not fill if price gaps |
| **Take Profit** | Market order at profit target | Profit taking | Slippage during fast moves |
| **TP Limit** | Limit order at profit target | Controlled profit taking | May not fill if price gaps |

### Configuration

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `BINANCE_API_KEY` | - | Binance API key |
| `BINANCE_API_SECRET` | - | Binance API secret |
| `BINANCE_TESTNET` | `true` | Use testnet (false for mainnet) |
| `SIMULATION_ENABLED` | `true` | Simulate orders (no real API calls) |
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection |
| `MONGODB_DATABASE` | `petrosa` | MongoDB database name |
| `NATS_URL` | `nats://localhost:4222` | NATS server URL |
| `NATS_TOPIC_SIGNALS` | `signals.trading.*` | NATS topic to consume |
| `STOP_LOSS_DEFAULT` | `2.0` | Default stop loss percentage |
| `TAKE_PROFIT_DEFAULT` | `5.0` | Default take profit percentage |

### Deployment

**Kubernetes Deployment:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: petrosa-tradeengine
  namespace: petrosa-apps
spec:
  replicas: 3
  selector:
    matchLabels:
      app: tradeengine
  template:
    spec:
      containers:
      - name: tradeengine
        image: yurisa2/petrosa-tradeengine:VERSION_PLACEHOLDER
        ports:
        - containerPort: 8000
          name: http
        env:
        - name: BINANCE_API_KEY
          valueFrom:
            secretKeyRef:
              name: petrosa-sensitive-credentials
              key: BINANCE_API_KEY
        - name: BINANCE_API_SECRET
          valueFrom:
            secretKeyRef:
              name: petrosa-sensitive-credentials
              key: BINANCE_API_SECRET
        - name: MONGODB_URL
          valueFrom:
            secretKeyRef:
              name: petrosa-sensitive-credentials
              key: MONGODB_URL
        - name: NATS_URL
          valueFrom:
            configMapKeyRef:
              name: petrosa-common-config
              key: NATS_URL
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
```

### Troubleshooting

**Common Issues:**

1. **Orders Rejected by Binance**
   - Check minimum notional value (usually $10)
   - Verify price precision
   - Check account balance

2. **API Rate Limit Errors**
   - Reduce trading frequency
   - Implement request queueing
   - Use WebSocket for price updates

3. **Failed Stop Loss/Take Profit**
   - Verify order type support
   - Check price levels validity
   - Review error logs

---

## 🔗 Data Manager Integration

The Trading Engine now integrates with `petrosa-data-manager` for centralized data access and persistence. This provides:

- **Centralized Data Access**: All market data and configuration through a single API
- **Consistent Data Models**: Standardized data structures across all services
- **Improved Reliability**: Built-in retry logic and circuit breakers
- **Better Monitoring**: Centralized logging and metrics for data operations

### Required Environment Variables

```bash
# Data Manager configuration
DATA_MANAGER_URL=http://petrosa-data-manager:8000
DATA_MANAGER_TIMEOUT=30
DATA_MANAGER_MAX_RETRIES=3
DATA_MANAGER_DATABASE=mongodb
```

### Data Manager Features

- **Generic CRUD API**: `/api/v1/{database}/{collection}` for flexible data operations
- **Domain-Specific API**: `/data/candles`, `/data/trades`, etc. for typed responses
- **Schema Registry**: Centralized schema management and validation
- **Audit Logging**: Comprehensive audit trails for all data operations

## 🚀 Quick Start

```bash
# Setup
make setup

# Run API server
make run

# Test trading (simulation mode)
curl -X POST http://localhost:8000/trade \
  -H "Content-Type: application/json" \
  -d '{"strategy_id": "test", "symbol": "BTCUSDT", "action": "buy", "confidence": 0.8, "price": 50000, "quantity": 0.001}'

# Deploy
make deploy
```

### Manual Deployment (No Code Changes)

For operational deployments without code changes (ConfigMap updates, Docker base image updates, etc.):
- **Guide**: See [docs/MANUAL_DEPLOYMENT_GUIDE.md](docs/MANUAL_DEPLOYMENT_GUIDE.md)
- **Trigger**: Via GitHub Actions UI or `gh workflow run manual-deploy.yml`

---

**Production Status:** ✅ **ACTIVE** - Processing 50-150 signals/day with comprehensive risk management
<!-- trigger deploy -->
