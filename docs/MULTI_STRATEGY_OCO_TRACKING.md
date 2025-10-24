# Multi-Strategy OCO Tracking

**Date**: October 24, 2025
**Version**: 2.1.0
**Status**: ✅ Implemented

## Overview

This document describes the architecture change that enables multiple strategies to place OCO (One-Cancels-Other) orders on the same Binance exchange position, with proper attribution tracking and independent P&L calculation.

---

## Problem Statement

### Before This Change

**Issue**: Duplicate OCO prevention blocked multiple strategies from having OCO protection.

```python
# Lines 94-108 in dispatcher.py (REMOVED)
if position_id in self.active_oco_pairs:
    return {"status": "skipped"}  # BLOCKED Strategy B!
```

**Scenario**:
```
Strategy A: Opens LONG 0.001 BTC @ $45k
  → OCO placed: TP=$48k, SL=$43k ✅

Strategy B: Adds LONG 0.002 BTC @ $46k
  → OCO SKIPPED (duplicate prevention) ❌
  → No TP/SL protection!
```

**Impact**:
- Only first strategy had OCO protection
- Subsequent strategies had no stop loss or take profit
- Unacceptable risk exposure

---

## Architecture Change

### Data Structure Change

**Before** (Single OCO per position):
```python
self.active_oco_pairs: Dict[str, Dict[str, Any]] = {}
# {position_id: oco_info}

active_oco_pairs = {
    "pos-123": {
        "sl_order_id": "sl_123",
        "tp_order_id": "tp_456",
        "status": "active"
    }
}
```

**After** (Multiple OCOs per exchange position):
```python
self.active_oco_pairs: Dict[str, List[Dict[str, Any]]] = {}
# {exchange_position_key: [oco_info1, oco_info2, ...]}

active_oco_pairs = {
    "BTCUSDT_LONG": [
        {
            "strategy_position_id": "uuid-a",
            "entry_price": 45000.0,
            "quantity": 0.001,
            "sl_order_id": "sl_123",
            "tp_order_id": "tp_456",
            "status": "active"
        },
        {
            "strategy_position_id": "uuid-b",
            "entry_price": 46000.0,
            "quantity": 0.002,
            "sl_order_id": "sl_789",
            "tp_order_id": "tp_012",
            "status": "active"
        }
    ]
}
```

**Key Changes**:
1. **Key**: Changed from `position_id` to `exchange_position_key` (e.g., "BTCUSDT_LONG")
2. **Value**: Changed from single dict to list of dicts
3. **Added fields**: `strategy_position_id`, `entry_price`, `quantity`

---

## How It Works

### 1. OCO Placement

**When Strategy A opens position:**
```python
# In _place_risk_management_orders()
strategy_position_id = self.order_to_strategy_position.get(order.order_id)
entry_price = result.get("fill_price", order.price)

await self.oco_manager.place_oco_orders(
    symbol="BTCUSDT",
    position_side="LONG",
    quantity=0.001,
    stop_loss_price=43000,
    take_profit_price=48000,
    strategy_position_id=strategy_position_id,  # NEW
    entry_price=45000,  # NEW - Strategy A's entry
)

# Result: OCO pair appended to list
active_oco_pairs["BTCUSDT_LONG"] = [
    {strategy_position_id: "uuid-a", entry_price: 45000, quantity: 0.001, ...}
]
```

**When Strategy B adds to position:**
```python
# Same process, different entry price
await self.oco_manager.place_oco_orders(
    ...,
    strategy_position_id=strategy_position_id_b,
    entry_price=46000,  # NEW - Strategy B's entry
)

# Result: Second OCO pair appended to same list
active_oco_pairs["BTCUSDT_LONG"] = [
    {strategy_position_id: "uuid-a", entry_price: 45000, ...},
    {strategy_position_id: "uuid-b", entry_price: 46000, ...}  # Added
]
```

### 2. OCO Monitoring

**Order monitoring loop** (runs every 2 seconds):
```python
async def _monitor_orders(self):
    for exchange_position_key, oco_list in self.active_oco_pairs.items():
        # Check each strategy's OCO pair independently
        for oco_info in oco_list:
            if oco_info["status"] != "active":
                continue

            # Check if SL or TP filled
            if tp_order_filled:
                await self._close_position_on_oco_completion(
                    filled_order_id=tp_order_id,
                    close_reason="take_profit",
                    oco_info=oco_info,  # Contains strategy context
                    dispatcher=self.dispatcher,
                )
```

### 3. Strategy Attribution & Closure

**When Strategy A's TP hits at $48k:**
```python
async def _close_position_on_oco_completion(..., oco_info):
    # Extract strategy context from oco_info
    strategy_position_id = oco_info["strategy_position_id"]  # uuid-a
    entry_price = oco_info["entry_price"]  # $45,000
    exit_quantity = oco_info["quantity"]  # 0.001

    # Get exit price from Binance
    exit_price = 48000  # From filled order

    # Calculate P&L using THIS strategy's entry price
    pnl = (48000 - 45000) * 0.001 = $3 profit

    # Close ONLY this strategy's position
    await strategy_position_manager.close_strategy_position(
        strategy_position_id="uuid-a",  # Only Strategy A
        exit_price=48000,
        exit_quantity=0.001,
        close_reason="take_profit",
        ...
    )

    # Cancel Strategy A's paired SL order
    await exchange.cancel_order(sl_order_id="sl_123")

    # Mark OCO as completed
    oco_info["status"] = "completed"

    # Export Prometheus metrics
    strategy_tp_triggered_total.inc()
    strategy_pnl_realized.observe(3.0)
```

**Result**:
- ✅ Strategy A: Closed, P&L = $3
- ✅ Strategy B: Still open, OCO orders still active
- ✅ Exchange position: Reduced from 0.003 to 0.002 BTC
- ✅ Metrics: Only Strategy A counts toward TP hit

---

## P&L Calculation

### Critical: Individual Entry Prices

**Example**:
```
Strategy A: Entry @ $45,000
Strategy B: Entry @ $46,000
Exchange Position: Weighted average @ $45,667

Price moves to $48,000:

Strategy A P&L:
  ($48,000 - $45,000) × 0.001 = $3.00 profit ✅ CORRECT

Strategy B (if closed):
  ($48,000 - $46,000) × 0.002 = $4.00 profit ✅ CORRECT

WRONG would be using weighted average ($45,667):
  ($48,000 - $45,667) × 0.001 = $2.33 ❌ INCORRECT
```

**Implementation**:
- Each OCO pair stores the strategy's **actual entry price**
- P&L calculated: `(exit_price - strategy_entry_price) * quantity`
- **NOT** using Binance's weighted average entry price

---

## Database Tracking

### MongoDB (PRIMARY)

**Collection**: `strategy_positions`

**Document Structure**:
```json
{
  "strategy_position_id": "uuid-a",
  "strategy_id": "test_momentum_v1",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "entry_price": 45000.0,
  "entry_quantity": 0.001,
  "entry_time": "2025-10-24T10:00:00Z",
  "take_profit_price": 48000.0,
  "stop_loss_price": 43000.0,
  "status": "open",
  "exchange_position_key": "BTCUSDT_LONG"
}
```

**On Closure**:
```json
{
  // ... existing fields ...
  "status": "closed",
  "exit_price": 48000.0,
  "exit_quantity": 0.001,
  "exit_time": "2025-10-24T10:15:00Z",
  "close_reason": "take_profit",
  "realized_pnl": 3.0,
  "realized_pnl_pct": 6.67,
  "duration_seconds": 900
}
```

### MySQL (SECONDARY BACKUP)

Same structure as MongoDB, synced via Data Manager API with best-effort delivery.

If MongoDB fails:
1. Position updates queued
2. Retry with exponential backoff
3. MySQL backup continues (non-critical)
4. Prometheus metrics still exported

---

## Prometheus Metrics

### New Metrics

1. **`tradeengine_strategy_oco_placed_total`**
   - Labels: `strategy_id`, `symbol`, `exchange`
   - When: OCO pair placed
   - Purpose: Track how many OCOs each strategy has placed

2. **`tradeengine_strategy_tp_triggered_total`**
   - Labels: `strategy_id`, `symbol`, `exchange`
   - When: Strategy's own TP order fills
   - Purpose: True TP hit rate per strategy

3. **`tradeengine_strategy_sl_triggered_total`**
   - Labels: `strategy_id`, `symbol`, `exchange`
   - When: Strategy's own SL order fills
   - Purpose: True SL hit rate per strategy

4. **`tradeengine_strategy_pnl_realized`** (Histogram)
   - Labels: `strategy_id`, `close_reason`, `exchange`
   - When: Strategy position closes
   - Purpose: P&L distribution per strategy

5. **`tradeengine_active_oco_pairs_per_position`** (Gauge)
   - Labels: `symbol`, `position_side`, `exchange`
   - When: OCO pairs added/removed
   - Purpose: Track multi-strategy positions

### Grafana Queries

**TP Hit Rate**:
```promql
(
  sum by (strategy_id) (increase(tradeengine_strategy_tp_triggered_total[24h]))
  /
  (
    sum by (strategy_id) (increase(tradeengine_strategy_tp_triggered_total[24h])) +
    sum by (strategy_id) (increase(tradeengine_strategy_sl_triggered_total[24h]))
  )
) * 100
```

**Multi-Strategy Positions**:
```promql
tradeengine_active_oco_pairs_per_position > 1
```

---

## Risk & Limitations

### ⚠️  Potential Race Condition

**Removed**: Duplicate OCO prevention (lines 94-108)

**Risk**: If Strategy B tries to place OCO before Strategy A's OCO completes, there could be a race condition.

**Mitigation**:
- OCO placement is fast (<500ms typically)
- Signals typically arrive seconds apart
- Each strategy tracks its own entry price independently
- If race occurs, second placement will fail gracefully (exchange error)

**Acceptable Risk**: This is a low-probability scenario with minimal impact. The benefit of multi-strategy OCO protection far outweighs this small risk.

### ⚠️  Order Management Complexity

**Consideration**: Each strategy adds 2 orders (SL + TP) to Binance.

**Example**:
- 3 strategies on same position = 6 orders on Binance
- Binance limit: 200 open orders per symbol (plenty of headroom)

### ⚠️  MongoDB Availability

**Requirement**: MongoDB must be available for position tracking.

**Fallback**:
- Queue updates for retry
- Exponential backoff (1s, 2s, 4s)
- MySQL backup continues
- Metrics still exported

---

## Testing

### Automated Test Script

**Location**: `scripts/test_multi_strategy_oco.py`

**What it tests**:
1. Single strategy creates one OCO pair
2. Second strategy adds its own OCO pair (no duplicate prevention)
3. Both strategies tracked separately with own entry prices
4. Exchange position aggregates both strategies

**Run**:
```bash
cd /Users/yurisa2/petrosa/petrosa-tradeengine
python scripts/test_multi_strategy_oco.py
```

### Manual Testing Scenarios

#### Scenario 1: Two Strategies, First TP Hits

1. Strategy A: LONG 0.001 @ $45k (TP=$48k, SL=$43k)
2. Strategy B: LONG 0.002 @ $46k (TP=$49k, SL=$44k)
3. Price moves to $48k
4. **Expected**:
   - Strategy A closes: P&L = $3
   - Strategy B remains open
   - Exchange position: 0.002 BTC remaining

#### Scenario 2: Second SL Hits

1. (Continue from Scenario 1)
2. Price drops to $44k
3. **Expected**:
   - Strategy B closes: P&L = -$4
   - Exchange position: 0 BTC (fully closed)

---

## Analytics Capabilities

### Per-Strategy Performance

**Query MongoDB**:
```python
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient(mongodb_uri)
db = client["petrosa"]

# Get all closed positions for a strategy
positions = await db.strategy_positions.find({
    "strategy_id": "momentum_v1",
    "status": "closed"
}).to_list(None)

# Calculate metrics
tp_hits = sum(1 for p in positions if p["close_reason"] == "take_profit")
sl_hits = sum(1 for p in positions if p["close_reason"] == "stop_loss")
total_pnl = sum(p.get("realized_pnl", 0) for p in positions)
avg_pnl = total_pnl / len(positions) if positions else 0

print(f"Strategy: momentum_v1")
print(f"  TP Hits: {tp_hits}")
print(f"  SL Hits: {sl_hits}")
print(f"  TP Hit Rate: {tp_hits / (tp_hits + sl_hits) * 100:.1f}%")
print(f"  Total P&L: ${total_pnl:,.2f}")
print(f"  Avg P&L: ${avg_pnl:,.2f}")
```

### Multi-Strategy Position Analysis

**Find positions with multiple strategies**:
```python
# Get all open exchange positions
from tradeengine.strategy_position_manager import strategy_position_manager

exchange_positions = strategy_position_manager.exchange_positions

for key, pos in exchange_positions.items():
    if pos.get("total_contributions", 0) > 1:
        print(f"\nMulti-Strategy Position: {key}")
        print(f"  Strategies: {pos['contributing_strategies']}")
        print(f"  Total Quantity: {pos['current_quantity']}")
        print(f"  Weighted Avg Price: ${pos['weighted_avg_price']:,.2f}")
```

---

## Grafana Dashboards

### Dashboard 1: Strategy Performance

**Prometheus Panels**:

1. **Active OCO Pairs** (Gauge)
   ```promql
   tradeengine_active_oco_pairs_per_position
   ```

2. **TP vs SL Hits** (Stacked bar)
   ```promql
   sum by (strategy_id) (increase(tradeengine_strategy_tp_triggered_total[24h]))
   sum by (strategy_id) (increase(tradeengine_strategy_sl_triggered_total[24h]))
   ```

3. **TP Hit Rate** (Gauge with threshold)
   ```promql
   (
     sum by (strategy_id) (increase(tradeengine_strategy_tp_triggered_total[24h]))
     /
     (
       sum by (strategy_id) (increase(tradeengine_strategy_tp_triggered_total[24h])) +
       sum by (strategy_id) (increase(tradeengine_strategy_sl_triggered_total[24h]))
     )
   ) * 100
   ```
   - Green: > 60%
   - Yellow: 40-60%
   - Red: < 40%

4. **Realized P&L Distribution** (Histogram)
   ```promql
   sum by (strategy_id, le) (rate(tradeengine_strategy_pnl_realized_bucket[1h]))
   ```

### Dashboard 2: OCO Tracking

**Prometheus Panels**:

1. **OCO Pairs Placed Today** (Stat)
   ```promql
   sum(increase(tradeengine_strategy_oco_placed_total[24h]))
   ```

2. **Multi-Strategy Positions Count** (Stat)
   ```promql
   count(tradeengine_active_oco_pairs_per_position > 1)
   ```

3. **P&L by Close Reason** (Time series)
   ```promql
   sum by (close_reason) (increase(tradeengine_strategy_pnl_realized_sum[1h]))
   ```

---

## Deployment

### Prerequisites

1. MongoDB Atlas connection configured in K8s secrets
2. `strategy_positions` table exists in MySQL (optional backup)
3. Prometheus metrics endpoint exposed
4. Grafana connected to Prometheus

### Deployment Steps

1. **Update Code**:
   ```bash
   cd /Users/yurisa2/petrosa/petrosa-tradeengine
   git add .
   git commit -m "feat: Multi-strategy OCO tracking with proper attribution"
   git push origin main
   ```

2. **CI/CD Pipeline**:
   - GitHub Actions will build Docker image
   - Push to registry
   - Update Kubernetes deployment
   - Restart pods

3. **Verify Deployment**:
   ```bash
   # Check pod status
   kubectl --kubeconfig=k8s/kubeconfig.yaml get pods -n petrosa-apps -l app=petrosa-tradeengine

   # Check logs
   kubectl --kubeconfig=k8s/kubeconfig.yaml logs -f deployment/petrosa-tradeengine -n petrosa-apps

   # Look for: "STARTING ORDER MONITORING (MULTI-STRATEGY MODE)"
   ```

4. **Verify Metrics**:
   ```bash
   # Port-forward to TradeEngine
   kubectl --kubeconfig=k8s/kubeconfig.yaml port-forward deployment/petrosa-tradeengine 8000:8000 -n petrosa-apps

   # Check metrics endpoint
   curl http://localhost:8000/metrics | grep strategy_

   # Should see:
   # tradeengine_strategy_oco_placed_total
   # tradeengine_strategy_tp_triggered_total
   # tradeengine_strategy_sl_triggered_total
   # tradeengine_strategy_pnl_realized
   # tradeengine_active_oco_pairs_per_position
   ```

---

## Monitoring & Troubleshooting

### Log Messages to Watch

**✅ Success Indicators**:
```
✅ OCO ORDERS PLACED SUCCESSFULLY
OCO pair added for strategy {strategy_position_id}: Total OCO pairs for {exchange_position_key}: 2
🎯 STRATEGY OCO TRIGGERED - CLOSING OWNING STRATEGY ONLY
✅ Strategy position {strategy_position_id} ({strategy_id}) closed: take_profit, P&L: $3.00
```

**⚠️  Warning Indicators**:
```
⚠️  No strategy_position_id in OCO info for {filled_order_id}
⚠️  Failed to cancel paired order {other_order_id}
```

**❌ Error Indicators**:
```
❌ No strategy_position_id in OCO info for {filled_order_id}
❌ Error closing position
❌ Failed to fetch order details
```

### Common Issues

#### Issue 1: OCO Not Placed for Second Strategy

**Symptom**: Only one OCO pair exists after two strategies open

**Diagnosis**:
```bash
# Check logs
kubectl logs -f deployment/petrosa-tradeengine -n petrosa-apps | grep "PLACING OCO"

# Should see TWO placement attempts
```

**Possible Causes**:
- Signal doesn't have stop_loss/take_profit
- _place_risk_management_orders not called
- strategy_position_id not mapped

**Solution**: Check signal data and verify mapping is created

#### Issue 2: Wrong Strategy Closes

**Symptom**: Both strategies close when one TP hits

**Diagnosis**:
```bash
# Check order monitoring logs
kubectl logs -f deployment/petrosa-tradeengine | grep "STRATEGY OCO TRIGGERED"

# Should identify specific strategy_position_id
```

**Possible Causes**:
- OCO info missing strategy_position_id
- _close_position_on_oco_completion not finding owning OCO

**Solution**: Verify OCO info structure in logs

#### Issue 3: Incorrect P&L

**Symptom**: P&L doesn't match expected calculation

**Diagnosis**:
- Check entry_price in OCO info (should be strategy's entry, not weighted avg)
- Check exit_price from filled order
- Verify P&L calculation formula

**MongoDB Query**:
```javascript
db.strategy_positions.find({
  strategy_position_id: "uuid-a"
})

// Verify:
// - entry_price matches signal entry
// - exit_price matches TP/SL price
// - realized_pnl = (exit - entry) * quantity (for LONG)
```

---

## References

- [Research Findings](RESEARCH_FINDINGS.md) - Implementation research
- [Strategy Position Manager](../tradeengine/strategy_position_manager.py) - Position tracking
- [Dispatcher](../tradeengine/dispatcher.py) - OCO management
- [Metrics](../tradeengine/metrics.py) - Prometheus metrics
- [Test Script](../scripts/test_multi_strategy_oco.py) - Automated testing
