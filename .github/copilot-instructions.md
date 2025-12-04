# GitHub Copilot Instructions - Trade Engine

## Service Context

**Purpose**: Order execution engine managing risk, coordinating trades, and enforcing safety limits.

**Deployment**: Kubernetes Deployment with MongoDB leader election (singleton for order coordination)

**Role in Ecosystem**: Receives orders → Risk checks → Binance API execution → Audit logging

---

## Architecture

**Data Flow**:
```
NATS (trade orders) → Trade Engine → Risk Management → Binance API → Trade Execution
                         ↓                                              ↓
                    MongoDB (config, locks)                     MongoDB (audit trail)
```

**Key Components**:
- `trade_engine/dispatcher/` - Order routing and coordination
- `trade_engine/execution/` - Binance API integration
- `trade_engine/risk/` - Position limits, exposure management
- `trade_engine/audit/` - Trade logging and compliance

---

## Service-Specific Patterns

### Distributed Locks

```python
# ✅ GOOD - Prevent duplicate order execution
async with distributed_lock(f"order:{order_id}"):
    await execute_order(order)

# Uses MongoDB for coordination across pods
```

### Risk Management

```python
# ✅ ALWAYS check before execution
if not await risk_manager.can_execute(order):
    logger.warning("Risk check failed", order_id=order.id)
    return RejectionReason.RISK_EXCEEDED

# Checks:
# - Position limits
# - Account exposure
# - Order size limits
# - Duplicate signal rejection
```

### OCO Orders

```python
# ✅ One-Cancels-Other logic
async def place_oco_order(stop_loss, take_profit):
    # Place both orders
    # When one fills, cancel the other
    # Atomic coordination via distributed lock
    pass
```

### Audit Trail

```python
# ✅ ALWAYS log trade execution
await audit_logger.log_trade({
    "order_id": order.id,
    "symbol": order.symbol,
    "side": order.side,
    "executed_price": fill_price,
    "timestamp": datetime.utcnow()
})
```

---

## Testing Patterns

```python
# Mock Binance API
@pytest.fixture
def mock_binance():
    with patch('trade_engine.execution.binance_client.BinanceClient') as mock:
        yield mock

# Test risk management
def test_risk_limit_enforcement():
    order = create_large_order()
    result = risk_manager.can_execute(order)
    assert result is False  # Exceeds limits

# Integration test with fake collaborators
def test_oco_order_coordination():
    # Test one-cancels-other logic
    pass
```

---

## Common Issues

**Duplicate Signals**: Distributed lock prevents duplicate execution
**Risk Violations**: Check position limits configuration
**Order Failures**: Review Binance API errors and retry logic

---

**Master Rules**: See `.cursorrules` in `petrosa_k8s` repo
**Service Rules**: `.cursorrules` in this repo
