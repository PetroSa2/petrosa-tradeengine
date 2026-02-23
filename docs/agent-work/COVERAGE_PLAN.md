# Test Coverage Improvement Plan - 5% Per Iteration

## Current Status
- **Overall Coverage**: 56% (target: 75%)
- **Remaining**: 19 percentage points
- **Strategy**: Move 5% per iteration (4 iterations: 5% + 5% + 5% + 4%)

## Iteration 1: 56% → 61% (Target: +5%)

### Focus Areas (Highest Impact)
1. **order_manager.py** (40% → 50%): +10% module coverage
   - Lines 79-104: `_setup_conditional_order` edge cases
   - Lines 110-147: `_monitor_conditional_order` error paths
   - Lines 153-164: `_check_condition` with different directions
   - Lines 169-185: `_get_current_price` cache logic
   - Lines 189-208: `_execute_conditional_order` full flow
   - **Expected overall impact**: ~2%

2. **dispatcher.py** (66% → 70%): +4% module coverage
   - Lines 1236, 1250-1254: Signal dispatch completion paths
   - Lines 237-238: OCO metrics (simple)
   - Lines 256-263: OCO failure logging (simple)
   - **Expected overall impact**: ~1.5%

3. **binance.py** (74% → 75%): +1% module coverage
   - Lines 808-810: Step size rounding edge cases
   - **Expected overall impact**: ~0.5%

4. **Other modules**: Add simple tests for uncovered utility functions
   - **Expected overall impact**: ~1%

### Tests to Add (Iteration 1)
- [ ] 10-12 order_manager conditional order tests
- [ ] 5-6 dispatcher completion/error path tests
- [ ] 2-3 binance edge case tests
- [ ] 3-4 utility function tests

**Total**: ~20-25 new tests

## Iteration 2: 61% → 66% (Target: +5%)

### Focus Areas
1. **order_manager.py** (50% → 60%): +10% module coverage
   - Remaining conditional order paths
   - Order cancellation edge cases
   - Order history queries

2. **dispatcher.py** (70% → 73%): +3% module coverage
   - OCO manager error paths
   - Position closing logic
   - Signal aggregation edge cases

3. **Integration tests**: Test interactions between modules
   - **Expected overall impact**: ~1%

### Tests to Add (Iteration 2)
- [ ] 8-10 order_manager integration tests
- [ ] 6-8 dispatcher integration tests
- [ ] 3-4 cross-module integration tests

**Total**: ~17-22 new tests

## Iteration 3: 66% → 71% (Target: +5%)

### Focus Areas
1. **dispatcher.py** (73% → 77%): +4% module coverage
   - Complex OCO scenarios
   - Position management integration
   - Error recovery paths

2. **order_manager.py** (60% → 65%): +5% module coverage
   - Complex conditional order scenarios
   - Timeout handling
   - Price monitoring edge cases

3. **Other modules**: Fill gaps in signal_aggregator, position_manager
   - **Expected overall impact**: ~1%

### Tests to Add (Iteration 3)
- [ ] 10-12 dispatcher complex scenario tests
- [ ] 8-10 order_manager complex scenario tests
- [ ] 4-5 other module tests

**Total**: ~22-27 new tests

## Iteration 4: 71% → 75% (Target: +4%)

### Focus Areas
1. **Final coverage push**: Target remaining uncovered lines
2. **Edge cases**: Complex error scenarios
3. **Integration**: End-to-end workflows

### Tests to Add (Iteration 4)
- [ ] 15-20 targeted edge case tests
- [ ] 5-8 integration workflow tests

**Total**: ~20-28 new tests

## Success Metrics
- Each iteration should add ~20-25 tests
- Each iteration should increase overall coverage by 4-5%
- All tests must pass
- Focus on high-impact areas first

## Notes
- Prioritize modules closest to their targets
- Focus on simple, high-impact tests first
- Skip complex tests that require extensive mocking (test indirectly)
- Use integration tests to cover complex interactions
