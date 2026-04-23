"""
Integration tests for algo order limit enforcement (#357).

Verifies:
- check_algo_order_limits counts ONLY open (active) orders, not filled/cancelled ones
- When OCO placement is rejected as duplicate (position already protected),
  no additional individual SL/TP orders are placed — preserving algo order headroom
- 10+ strategy signals for the same symbol do not exhaust the Binance algo order limit
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from contracts.signal import Signal, SignalStrength, SignalType, StrategyMode
from shared.constants import UTC
from tests.integration.fakes import FakeExchange, FakePositionManager
from tradeengine.dispatcher import Dispatcher
from tradeengine.position_manager import PositionManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_distributed_lock():
    """Bypass MongoDB distributed lock for all tests in this module."""

    async def mock_execute_with_lock(lock_key, func, *args, **kwargs):
        return await func(*args, **kwargs)

    with patch(
        "tradeengine.dispatcher.distributed_lock_manager.execute_with_lock",
        side_effect=mock_execute_with_lock,
    ):
        yield


@pytest.fixture(autouse=True)
def mock_audit_logger():
    """Disable audit logger to avoid external dependencies."""
    with patch("shared.audit.audit_logger.enabled", False):
        with patch("shared.audit.audit_logger.connected", False):
            yield


@pytest.fixture
def mock_exchange_with_algo_orders():
    """Exchange mock with controllable open algo order counts."""
    exchange = MagicMock()
    exchange.get_account_info = AsyncMock(return_value={"available_balance": 10000.0})
    exchange.get_open_algo_orders = AsyncMock()
    return exchange


@pytest.fixture
def position_manager_with_mock_exchange(mock_exchange_with_algo_orders):
    pm = PositionManager(exchange=mock_exchange_with_algo_orders)
    pm.total_portfolio_value = 10000.0
    return pm


@pytest.fixture
def dispatcher_with_fakes():
    fake_exchange = FakeExchange()
    fake_position_mgr = FakePositionManager(
        max_position_size_pct=0.5,
        max_daily_loss_pct=0.1,
        max_portfolio_exposure_pct=0.99,
        total_portfolio_value=100_000.0,
    )
    dispatcher = Dispatcher(exchange=fake_exchange)
    dispatcher.position_manager = fake_position_mgr
    return dispatcher, fake_exchange, fake_position_mgr


def _make_signal(
    strategy_id: str,
    symbol: str = "XRPUSDT",
    confidence: float = 0.85,
    quantity: float = 100.0,
    price: float = 0.60,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        action="buy",
        signal_type=SignalType.BUY,
        confidence=confidence,
        strength=SignalStrength.STRONG,
        timeframe="1h",
        price=price,
        quantity=quantity,
        current_price=price,
        timestamp=datetime.now(UTC),
        source="petrosa-cio",
        strategy=strategy_id,
        strategy_mode=StrategyMode.DETERMINISTIC,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
    )


# ---------------------------------------------------------------------------
# AC-1: Order Count Audit — only OPEN orders are counted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_algo_order_limits_counts_only_open_orders(
    position_manager_with_mock_exchange, mock_exchange_with_algo_orders
):
    """
    check_algo_order_limits must query the exchange's open-orders endpoint.
    Filled or cancelled orders must never be included in the count.
    Even if 50 orders have previously been filled for a symbol, the limit check
    should only see the currently-open ones.
    """
    # Simulate: many historical orders were filled but only 3 are currently open
    mock_exchange_with_algo_orders.get_open_algo_orders.side_effect = [
        [{"algoId": i} for i in range(3)],  # symbol-level: 3 open (not 50 historical)
        [{"algoId": i} for i in range(3)],  # account-level: 3 open total
    ]

    order = TradeOrder(
        symbol="XRPUSDT", side="buy", type="market", amount=100.0, position_side="LONG"
    )

    result = await position_manager_with_mock_exchange.check_algo_order_limits(order)

    assert result is True, "Should allow order when only 3 open orders exist"
    # Confirm we called the exchange API (not an internal counter)
    assert mock_exchange_with_algo_orders.get_open_algo_orders.call_count == 2


@pytest.mark.asyncio
async def test_check_algo_order_limits_rejects_at_threshold(
    position_manager_with_mock_exchange, mock_exchange_with_algo_orders
):
    """
    Reject when 9 or more open algo orders exist for a symbol (leaving <2 free slots).
    """
    mock_exchange_with_algo_orders.get_open_algo_orders.side_effect = [
        [{"algoId": i} for i in range(9)],  # 9 open orders for symbol
    ]

    order = TradeOrder(
        symbol="XRPUSDT", side="buy", type="market", amount=100.0, position_side="LONG"
    )

    result = await position_manager_with_mock_exchange.check_algo_order_limits(order)

    assert result is False, "Should reject when 9+ open orders exist for symbol"
    # Account-level check should be skipped once symbol check fails
    assert mock_exchange_with_algo_orders.get_open_algo_orders.call_count == 1


@pytest.mark.asyncio
async def test_check_algo_order_limits_allows_8_open_orders(
    position_manager_with_mock_exchange, mock_exchange_with_algo_orders
):
    """
    Allow when exactly 8 open orders exist for a symbol (2 free slots available).
    """
    mock_exchange_with_algo_orders.get_open_algo_orders.side_effect = [
        [{"algoId": i} for i in range(8)],  # 8 open → 2 slots free → OK
        [{"algoId": i} for i in range(8)],  # account check
    ]

    order = TradeOrder(
        symbol="XRPUSDT", side="buy", type="market", amount=100.0, position_side="LONG"
    )

    result = await position_manager_with_mock_exchange.check_algo_order_limits(order)

    assert result is True, "Should allow when 8 open orders exist (2 slots free)"


# ---------------------------------------------------------------------------
# AC-2: Duplicate OCO must NOT fall back to individual SL/TP orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_oco_does_not_place_individual_orders(dispatcher_with_fakes):
    """
    When a second strategy signal enters for a symbol that already has an active
    OCO pair, the dispatcher must NOT place individual SL/TP orders as a fallback.
    Placing individual orders would consume 2 more algo order slots per strategy,
    exhausting the Binance 10-order-per-symbol limit.
    """
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_fakes

    # Seed OCO manager with an existing active OCO pair for XRPUSDT_LONG
    # so the second signal triggers the duplicate guard
    dispatcher.oco_manager.active_oco_pairs["XRPUSDT_LONG"] = [
        {
            "position_id": "strategy_a_pos",
            "strategy_position_id": "strategy_a_pos",
            "entry_price": 0.60,
            "quantity": 100.0,
            "sl_order_id": "existing_sl_001",
            "tp_order_id": "existing_tp_001",
            "symbol": "XRPUSDT",
            "position_side": "LONG",
            "status": "active",
            "created_at": 1_700_000_000.0,
        }
    ]

    # Count individual risk orders (stop/take_profit) placed BEFORE second signal
    initial_executed = len(fake_exchange.get_executed_orders())

    # Second strategy signal for the same symbol — should be accepted as entry
    # but OCO placement should be CONSOLIDATED (not fall back to individual orders)
    signal_b = _make_signal("strategy-b", symbol="XRPUSDT")

    result = await dispatcher.dispatch(signal_b)

    # FakeExchange.execute() is called for EVERY order the dispatcher places,
    # including individual SL/TP fallback orders from _place_individual_risk_orders.
    # With the fix: only the entry market order should be recorded (≤1 new call).
    # Without the fix: 2 additional calls (SL + TP) would appear, giving ≥3 total.
    new_orders = fake_exchange.get_executed_orders()[initial_executed:]
    # SL/TP individual orders use order_ids prefixed "oco_sl_" / "oco_tp_" when placed
    # via place_oco_orders, and are tracked via exchange.execute(). Any result beyond
    # the single entry order indicates unwanted individual risk-order placement.
    assert len(new_orders) <= 1, (
        f"Expected ≤1 new exchange.execute() call (entry only) when duplicate OCO is "
        f"detected, got {len(new_orders)}: {new_orders}. Extra calls indicate the "
        f"individual SL/TP fallback fired despite existing OCO coverage."
    )


# ---------------------------------------------------------------------------
# AC-2 (limit strategy): 10+ signals don't accumulate algo orders past limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ten_strategy_signals_stay_within_algo_order_limit(dispatcher_with_fakes):
    """
    Simulate 10 different strategies sending BUY signals for the same symbol.
    After all signals are processed, the number of algo orders placed on the
    exchange must not exceed 2 (1 OCO pair = 1 SL + 1 TP).

    Without the fix, each strategy after the first would fall back to placing
    individual SL/TP orders, yielding up to 2 × 10 = 20 algo orders — exceeding
    the Binance limit of 10 per symbol.
    """
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_fakes

    strategy_ids = [f"strategy-{i}" for i in range(10)]

    results = []
    for sid in strategy_ids:
        sig = _make_signal(sid, symbol="XLMUSDT")
        result = await dispatcher.dispatch(sig)
        results.append(result)

    all_executed = fake_exchange.get_executed_orders()

    # Verify via OCO manager state: at most 1 active OCO pair for this symbol/side.
    oco_pairs = dispatcher.oco_manager.active_oco_pairs
    xlm_long_pairs = oco_pairs.get("XLMUSDT_LONG", [])
    active_pairs = [p for p in xlm_long_pairs if p.get("status") == "active"]

    assert len(active_pairs) <= 1, (
        f"Expected at most 1 active OCO pair for XLMUSDT_LONG after 10 strategy signals, "
        f"got {len(active_pairs)}. Multiple OCO pairs indicate the dedup guard is not "
        f"preventing duplicate placements."
    )

    # Verify via exchange execution count: FakeExchange.execute() is called for every
    # order placed (entry + OCO SL + OCO TP + any individual fallback orders).
    # With fix: 1 OCO pair = 2 risk orders max, regardless of how many strategy signals fired.
    # Each entry order = 1 call. Risk orders per fix = at most 2 (1 SL + 1 TP for the first
    # signal only). Subsequent signals that hit duplicate_oco should add 0 risk orders.
    # So total = N_entry_orders + 2 (at most).
    n_entry_orders = sum(
        1 for r in results if r.get("status") not in ("rejected", "duplicate", "error")
    )
    n_risk_order_calls = len(all_executed) - n_entry_orders
    assert n_risk_order_calls <= 2, (
        f"Expected at most 2 risk-order exchange.execute() calls (1 SL + 1 TP for the "
        f"first OCO pair), got {n_risk_order_calls} across {len(strategy_ids)} signals. "
        f"This indicates individual SL/TP fallback fired for duplicate-OCO positions."
    )
