# Trade Execution Filters System

## Overview

The trade execution filters system provides fine-grained control over which trades are allowed to execute based on configurable parameters. This system implements a hierarchical filter mechanism that allows setting different filter rules at different levels of specificity.

## Filter Hierarchy

The system implements a 5-layer filter hierarchy with the following priority (highest to lowest):

1. **Per-Side Filters** - Specific to symbol and position side (e.g., BTCUSDT-LONG)
2. **Per-Strategy Filters** - Specific to trading strategy (e.g., momentum_strategy)
3. **Per-Pair Filters** - Specific to trading pair (e.g., BTCUSDT)
4. **Global Filters** - Applied to all trades
5. **Hardcoded Defaults** - Built-in safety limits

In implementation this is applied bottom-up: defaults -> global -> per-pair -> per-strategy -> per-side.

## Available Filter Parameters

### Take Profit Distance Filters
- `tp_distance_min_pct` - Minimum take profit distance (%)
- `tp_distance_max_pct` - Maximum take profit distance (%)

### Stop Loss Distance Filters
- `sl_distance_min_pct` - Minimum stop loss distance (%)
- `sl_distance_max_pct` - Maximum stop loss distance (%)

### Price Range Filters
- `price_min_absolute` - Minimum absolute price
- `price_max_absolute` - Maximum absolute price
- `price_min_relative_pct` - Minimum price relative to market (%)
- `price_max_relative_pct` - Maximum price relative to market (%)

### Quantity Filters
- `quantity_min` - Minimum order quantity
- `quantity_max` - Maximum order quantity

### Position Side Filters
- `enabled_sides` - List of allowed sides ["LONG", "SHORT"]

## API Endpoints

### Per-Strategy Filters
```
GET /api/v1/config/filters/strategy/{strategy_id}
PUT /api/v1/config/filters/strategy/{strategy_id}
```

Note: This module currently implements strategy-scoped filter endpoints only.

## Configuration Examples

### Setting Strategy-Specific Filters
```bash
curl -X PUT http://localhost:8000/api/v1/config/filters/strategy/momentum_strategy \
  -H "Content-Type: application/json" \
  -d '{
    "filters": {
      "tp_distance_min_pct": 2.0,
      "tp_distance_max_pct": 15.0,
      "enabled_sides": ["LONG"]
    },
    "changed_by": "admin",
    "reason": "Momentum strategy prefers longer TP distances and LONG positions only"
  }'
```

## Filter Resolution Process

When evaluating a trade, the system resolves filters in the following order:

1. Start with hardcoded defaults
2. Apply global filters
3. Apply per-pair filters
4. Apply per-strategy filters
5. Apply per-side filters

At each step, more specific filters override less specific ones.

## Database Storage

Filter configurations are stored in MongoDB collections:
- `trading_configs_global` - Global filter settings
- `trading_configs_symbols` - Per-pair filter settings
- `trading_configs_strategy` - Per-strategy filter settings
- `trading_configs_symbol_side` - Per-side filter settings

Each configuration includes full audit trail information.

## Integration Points

The filter system integrates with:
- Trade execution engine (validates orders before submission)
- Signal processing (filters incoming trade signals)
- Risk management (enforces position limits)
- Strategy modules (strategy-specific constraints)

## Best Practices

1. **Start Conservative** - Begin with tight filters and loosen as needed
2. **Monitor Closely** - Watch rejected trades to tune filters appropriately
3. **Document Changes** - Always provide reasons for filter modifications
4. **Test Incrementally** - Make small adjustments and observe results
5. **Use Strategy Filters** - Customize per strategy rather than using global filters when possible

## Troubleshooting

### Common Issues

1. **Too Many Rejected Trades** - Loosen filter constraints or check market conditions
2. **No Effect from Filter Changes** - Verify cache has refreshed (60-second TTL)
3. **Strategy Filters Not Applying** - Confirm strategy_id matches exactly

### Debugging Steps

1. Check current filter configuration:
   ```bash
   curl http://localhost:8000/api/v1/config/filters/strategy/momentum_strategy
   ```

2. Review audit trail:
   ```bash
   # Connect to MongoDB and check trading_configs_audit collection
   ```

3. Check application logs for filter rejection messages
