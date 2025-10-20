# Trading Configuration API - Quick Reference

## Base URL
```
http://petrosa-tradeengine:8080/api/v1/config
```

## Quick Start

### 1. Discover Available Parameters
```bash
curl http://localhost:8000/api/v1/config/trading/schema | jq .
```

Returns all 31 parameters with descriptions, defaults, validation rules, and usage guidance.

### 2. Get Current Global Config
```bash
curl http://localhost:8000/api/v1/config/trading | jq .
```

### 3. Update Global Config
```bash
curl -X POST http://localhost:8000/api/v1/config/trading \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 15,
      "stop_loss_pct": 2.5,
      "risk_management_enabled": true
    },
    "changed_by": "admin",
    "reason": "Adjusting risk parameters for current market"
  }' | jq .
```

## Common Use Cases

### Configure Specific Symbol
```bash
# Get BTCUSDT config
curl http://localhost:8000/api/v1/config/trading/BTCUSDT | jq .

# Update BTCUSDT config
curl -X POST http://localhost:8000/api/v1/config/trading/BTCUSDT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 20,
      "position_size_pct": 0.15,
      "stop_loss_pct": 1.5
    },
    "changed_by": "trader_bot",
    "reason": "BTC-specific settings for high volatility"
  }' | jq .
```

### Configure Symbol-Side (Most Specific)
```bash
# Configure BTCUSDT LONG positions only
curl -X POST http://localhost:8000/api/v1/config/trading/BTCUSDT/LONG \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 25,
      "take_profit_pct": 8.0,
      "enable_longs": true
    },
    "changed_by": "llm_agent_v1",
    "reason": "Bullish setup for BTC longs"
  }' | jq .

# Configure BTCUSDT SHORT positions
curl -X POST http://localhost:8000/api/v1/config/trading/BTCUSDT/SHORT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 10,
      "stop_loss_pct": 3.0,
      "take_profit_pct": 5.0
    },
    "changed_by": "llm_agent_v1",
    "reason": "Conservative settings for BTC shorts"
  }' | jq .
```

### Disable Trading for Specific Symbol/Side
```bash
# Disable ETHUSDT shorts temporarily
curl -X POST http://localhost:8000/api/v1/config/trading/ETHUSDT/SHORT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "enabled": false
    },
    "changed_by": "risk_manager",
    "reason": "Pausing ETH shorts due to high volatility"
  }' | jq .
```

### Validate Parameters Before Saving
```bash
curl -X POST http://localhost:8000/api/v1/config/trading \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 150,
      "stop_loss_pct": -5.0
    },
    "changed_by": "test",
    "validate_only": true
  }' | jq .
```

Returns validation errors without saving.

### Delete Configuration (Revert to Parent)
```bash
# Delete symbol-side config (reverts to symbol or global)
curl -X DELETE "http://localhost:8000/api/v1/config/trading/BTCUSDT/LONG?changed_by=admin&reason=Reverting%20to%20default"

# Delete symbol config (reverts to global)
curl -X DELETE "http://localhost:8000/api/v1/config/trading/BTCUSDT?changed_by=admin&reason=Resetting%20BTC%20config"
```

## Key Parameters Reference

### High-Impact Parameters
```json
{
  "leverage": 10,              // 1-125x multiplier
  "position_size_pct": 0.1,    // 10% of portfolio per trade
  "stop_loss_pct": 2.0,        // 2% stop loss
  "take_profit_pct": 5.0,      // 5% take profit
  "risk_management_enabled": true
}
```

### Position Sizing
```json
{
  "position_size_pct": 0.1,           // % of portfolio
  "max_position_size_usd": 1000.0,    // Hard cap in USD
  "min_position_size_usd": 10.0,      // Minimum trade size
  "quantity_multiplier": 1.0,         // Scale all positions
  "use_exchange_minimums": true       // Respect Binance limits
}
```

### Risk Limits
```json
{
  "max_daily_loss_pct": 0.05,        // 5% daily loss limit
  "max_portfolio_exposure_pct": 0.8,  // 80% max exposure
  "max_daily_trades": 100,            // Trade frequency limit
  "max_concurrent_positions": 10      // Position count limit
}
```

### Direction Control
```json
{
  "enabled": true,         // Master switch
  "enable_longs": true,    // Allow LONG positions
  "enable_shorts": true    // Allow SHORT positions
}
```

## Configuration Hierarchy

Settings are resolved in this order:
1. **Symbol-Side** (e.g., BTCUSDT-LONG) - Most specific
2. **Symbol** (e.g., BTCUSDT)
3. **Global** - Applies to all
4. **Defaults** - Hardcoded fallback

Example:
```
Global:        leverage=10
BTCUSDT:       leverage=15  ← Overrides global
BTCUSDT-LONG:  leverage=20  ← Overrides BTCUSDT and global

Result for BTCUSDT LONG: leverage=20
Result for BTCUSDT SHORT: leverage=15
Result for ETHUSDT: leverage=10
```

## Response Format

### Success Response
```json
{
  "success": true,
  "data": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "parameters": {...},
    "version": 3,
    "source": "mongodb",
    "created_at": "2025-10-20T10:00:00Z",
    "updated_at": "2025-10-20T15:30:00Z"
  },
  "metadata": {
    "action": "updated",
    "scope": "symbol_side"
  }
}
```

### Error Response
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Parameter validation failed",
    "details": {
      "errors": [
        "leverage must be <= 125, got 150",
        "stop_loss_pct must be >= 0.1, got -5.0"
      ]
    }
  }
}
```

## LLM Agent Examples

### Conservative Mode
```bash
curl -X POST http://localhost:8000/api/v1/config/trading \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 5,
      "position_size_pct": 0.05,
      "stop_loss_pct": 1.5,
      "take_profit_pct": 3.0,
      "max_daily_trades": 20,
      "max_concurrent_positions": 5
    },
    "changed_by": "llm_risk_manager",
    "reason": "Switching to conservative mode due to high market uncertainty"
  }'
```

### Aggressive Mode (Bull Market)
```bash
curl -X POST http://localhost:8000/api/v1/config/trading \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 20,
      "position_size_pct": 0.15,
      "stop_loss_pct": 2.5,
      "take_profit_pct": 8.0,
      "max_concurrent_positions": 15,
      "enable_shorts": false
    },
    "changed_by": "llm_market_analyzer",
    "reason": "Strong bull market detected, optimizing for longs only"
  }'
```

### Symbol-Specific Tuning
```bash
# ETH needs tighter stops due to volatility
curl -X POST http://localhost:8000/api/v1/config/trading/ETHUSDT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "stop_loss_pct": 1.0,
      "slippage_tolerance_pct": 0.5
    },
    "changed_by": "llm_volatility_manager",
    "reason": "ETH showing high volatility, tightening risk controls"
  }'
```

## Health Check
```bash
curl http://localhost:8000/api/v1/config/health | jq .
```

Returns:
```json
{
  "status": "healthy",
  "mongodb_connected": true,
  "cache_size": 5
}
```

## Tips

1. **Always use validate_only first** when testing new parameters
2. **Start with global config** then add symbol/side overrides
3. **Use descriptive reasons** for audit trail
4. **Check schema** before modifying unknown parameters
5. **Cache TTL is 60 seconds** - changes take effect within 1 minute
6. **Leverage changes may fail** if positions are open (system continues with existing leverage)

## Port Forwarding (for local testing)
```bash
kubectl --kubeconfig=k8s/kubeconfig.yaml port-forward -n petrosa-apps \
  svc/petrosa-tradeengine 8000:80

# Then use localhost:8000 for all requests
curl http://localhost:8000/api/v1/config/trading/schema
```

## Complete Example: Setting Up New Symbol

```bash
# 1. Check schema
curl http://localhost:8000/api/v1/config/trading/schema | jq '.data.leverage'

# 2. Set symbol config
curl -X POST http://localhost:8000/api/v1/config/trading/SOLUSDT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 12,
      "position_size_pct": 0.08,
      "stop_loss_pct": 2.5,
      "take_profit_pct": 6.0,
      "use_exchange_minimums": true
    },
    "changed_by": "setup_script",
    "reason": "Initial SOL configuration"
  }'

# 3. Customize LONG positions
curl -X POST http://localhost:8000/api/v1/config/trading/SOLUSDT/LONG \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 15,
      "take_profit_pct": 8.0
    },
    "changed_by": "setup_script",
    "reason": "Higher targets for SOL longs"
  }'

# 4. Customize SHORT positions
curl -X POST http://localhost:8000/api/v1/config/trading/SOLUSDT/SHORT \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {
      "leverage": 8,
      "stop_loss_pct": 1.5
    },
    "changed_by": "setup_script",
    "reason": "Tighter stops for SOL shorts"
  }'

# 5. Verify configuration
curl http://localhost:8000/api/v1/config/trading/SOLUSDT/LONG | jq '.data.parameters'
```

---

**Quick Reference Generated:** October 20, 2025
**API Version:** 1.1.0
**Total Endpoints:** 12+
**Total Parameters:** 31
