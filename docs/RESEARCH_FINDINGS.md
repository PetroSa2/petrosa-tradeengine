# Research Findings - Multi-Strategy OCO Tracking

## Date: 2025-10-24

## Research Summary

### 1. position_client Provision

**Location**: `shared/mysql_client.py`

**Type**: `DataManagerPositionClient` class

**How it works**:
- Uses Data Manager API as proxy to MySQL
- Global instance: `position_client = DataManagerPositionClient()`
- Imported in:
  - `tradeengine/position_manager.py`
  - `tradeengine/strategy_position_manager.py`

**Methods Available**:
- `create_position(position_data)` - Create new position
- `update_position(position_id, update_data)` - Update existing
- `get_position(position_id)` - Retrieve by ID
- `get_open_positions(strategy_id)` - Query open positions

**Status**: ✅ Fully configured and working

---

### 2. MySQL Table: strategy_positions

**SQL File**: `scripts/create_strategy_positions_table.sql`

**Status**: ✅ Schema exists

**Key Fields**:
- `strategy_position_id` - UUID primary key
- `strategy_id` - Strategy identifier
- `entry_price` - **Critical**: Strategy's actual entry price
- `exit_price` - Exit price
- `realized_pnl` - P&L calculated from entry_price
- `close_reason` - ENUM('take_profit', 'stop_loss', 'manual', 'partial', 'liquidation')

**Note**: MySQL is used as SECONDARY backup via Data Manager API

---

### 3. strategy_position_manager MongoDB Connection

**Location**: `tradeengine/strategy_position_manager.py`

**Connection Method**: Uses `position_client` (Data Manager API) - NO direct MongoDB connection

**Storage Pattern**:
```python
# In-memory tracking
self.strategy_positions: dict[str, dict] = {}

# Persistence via Data Manager
await position_client.create_position(strategy_position)
await position_client.update_position(strategy_position_id, position)
```

**MongoDB Configuration**:
- Connection string: K8s secret `mongodb-connection-string`
- Database: K8s secret `MONGODB_DATABASE`
- Accessed via `position_manager.py` for coordination

**Status**: ✅ MongoDB used for coordination, Data Manager for persistence

---

### 4. position_manager MongoDB Integration

**Location**: `tradeengine/position_manager.py`

**Connection**:
```python
async def _initialize_mongodb(self):
    mongodb_url = get_mongodb_connection_string()  # From K8s secret
    database_name = MONGODB_DATABASE  # From K8s secret

    self.mongodb_client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_url)
    self.mongodb_db = self.mongodb_client[database_name]
```

**Usage**: Distributed coordination only (not primary storage)

**Status**: ✅ Configured via K8s secrets/configmap

---

## Key Insights

### Current Architecture

```
Signal → Dispatcher → process_signal()
         ↓
    Create position
         ↓
    strategy_position_manager.create_strategy_position()
         ↓
    In-memory: self.strategy_positions[id] = {...}
         ↓
    Persist: position_client.create_position() → Data Manager → MySQL
         ↓
    Place OCO orders
         ↓
    OCO Manager (PROBLEM: blocks duplicate OCOs)
```

### The Problem

**Lines 94-108 in dispatcher.py**:
```python
if position_id in self.active_oco_pairs:
    return {"status": "skipped"}  # Blocks Strategy B!
```

**Impact**: Only first strategy gets OCO protection

---

## Implementation Plan Validation

✅ **MongoDB**: Primary via Data Manager API
✅ **MySQL**: Secondary backup via Data Manager API
✅ **position_client**: Configured and working
✅ **strategy_positions table**: Schema exists
✅ **K8s secrets**: mongodb-connection-string, MONGODB_DATABASE available

**Ready to proceed with implementation**
