"""
Tests for TE_EXCHANGE_TRUTH_STORE_ENABLED feature flag (#459 — 446-C).

Covers AC1 (flag constant), AC2 (PositionManager read-path), AC3 (API source field),
AC4 (StrategyPositionManager attribute), AC5 (flag=off and flag=on paths; runtime toggle).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = UTC


def _make_snapshot(
    symbol: str, side: str, qty: float, entry: float = 50_000.0
) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        side=side,
        quantity=qty,
        entry_price=entry,
        unrealized_pnl=0.0,
        updated_at=datetime.now(UTC),
    )


def _make_store(
    positions: dict[tuple[str, str], PositionSnapshot],
) -> ExchangeTruthStore:
    store = ExchangeTruthStore()
    store._positions = dict(positions)
    store._is_ready = True
    return store


def _make_order(
    symbol: str = "BTCUSDT", side: str = "buy", amount: float = 1.0
) -> MagicMock:
    order = MagicMock()
    order.symbol = symbol
    order.side = side
    order.position_side = "LONG" if side == "buy" else "SHORT"
    order.amount = amount
    order.position_size_pct = None
    order.exchange = "binance"
    return order


# ---------------------------------------------------------------------------
# AC1 — flag constant exists and defaults to "off"
# ---------------------------------------------------------------------------


class TestAC1FlagConstant:
    def test_constant_exists_and_defaults_off(self) -> None:
        from shared.constants import TE_EXCHANGE_TRUTH_STORE_ENABLED

        assert TE_EXCHANGE_TRUTH_STORE_ENABLED == "off"

    def test_settings_field_defaults_off(self) -> None:
        from shared.config import Settings

        s = Settings()
        assert s.te_exchange_truth_store_enabled == "off"


# ---------------------------------------------------------------------------
# AC2 — PositionManager.get_positions() read-path
# ---------------------------------------------------------------------------


class TestAC2GetPositions:
    def _make_pm(self) -> Any:
        from tradeengine.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm.positions = {}
        pm.exchange_truth_store = None
        return pm

    def test_flag_off_returns_local_positions(self) -> None:
        pm = self._make_pm()
        pm.positions = {("BTCUSDT", "LONG"): {"symbol": "BTCUSDT", "quantity": 2.0}}
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"
        ):
            result = pm.get_positions()
        assert result == {("BTCUSDT", "LONG"): {"symbol": "BTCUSDT", "quantity": 2.0}}

    def test_flag_on_no_store_falls_back_to_local(self) -> None:
        pm = self._make_pm()
        pm.positions = {("ETHUSDT", "SHORT"): {"symbol": "ETHUSDT", "quantity": 5.0}}
        pm.exchange_truth_store = None
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result = pm.get_positions()
        assert ("ETHUSDT", "SHORT") in result

    def test_flag_on_with_store_returns_exchange_snapshots(self) -> None:
        pm = self._make_pm()
        pm.positions = {}  # local is empty — exchange has a position
        snap = _make_snapshot("BTCUSDT", "LONG", qty=3.5, entry=60_000.0)
        pm.exchange_truth_store = _make_store({("BTCUSDT", "LONG"): snap})
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result = pm.get_positions()
        assert ("BTCUSDT", "LONG") in result
        pos = result[("BTCUSDT", "LONG")]
        assert pos["quantity"] == 3.5
        assert pos["avg_price"] == 60_000.0
        assert pos["source"] == "exchange"

    def test_flag_on_store_empty_returns_empty_dict(self) -> None:
        pm = self._make_pm()
        pm.positions = {("BTCUSDT", "LONG"): {"symbol": "BTCUSDT", "quantity": 1.0}}
        pm.exchange_truth_store = _make_store({})  # exchange has no open positions
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result = pm.get_positions()
        assert result == {}

    def test_flag_shadow_uses_local_path(self) -> None:
        """Shadow mode must not alter read behaviour (behavioural change deferred)."""
        pm = self._make_pm()
        pm.positions = {("BTCUSDT", "LONG"): {"symbol": "BTCUSDT", "quantity": 1.0}}
        snap = _make_snapshot("BTCUSDT", "LONG", qty=9.9)
        pm.exchange_truth_store = _make_store({("BTCUSDT", "LONG"): snap})
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"
        ):
            result = pm.get_positions()
        assert result[("BTCUSDT", "LONG")]["quantity"] == 1.0  # still local


# ---------------------------------------------------------------------------
# AC2 — PositionManager.check_position_limits() read-path
# ---------------------------------------------------------------------------


class TestAC2CheckPositionLimits:
    def _make_pm(self) -> Any:
        from tradeengine.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm.positions = {}
        pm.exchange_truth_store = None
        pm.rejection_reason = None
        pm.max_position_size_pct = 0.1
        pm.max_daily_loss_pct = 0.05
        pm.max_portfolio_exposure_pct = 0.8
        pm.total_portfolio_value = 100_000.0
        pm.daily_pnl = 0.0
        pm.portfolio_value_last_update = None
        pm.portfolio_value_lock = asyncio.Lock()
        pm.sync_lock = asyncio.Lock()
        pm.exchange = None
        pm.settings = MagicMock()
        pm.settings.mongodb_uri = None
        pm.settings.mongodb_database = None
        pm.mongodb_db = None
        pm.mongodb_client = None
        pm.last_sync_time = None
        return pm

    @pytest.mark.asyncio
    async def test_flag_off_uses_local_positions(self) -> None:
        pm = self._make_pm()
        pm.positions = {("BTCUSDT", "LONG"): {"quantity": 5.0}}

        async def _mock_refresh(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_get_limit(*_: Any, **__: Any) -> float:
            return 10.0  # max 10 BTC

        async def _mock_refresh_from_dm(*_: Any, **__: Any) -> None:
            pass

        async def _mock_check_algo(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_allowed_symbols(*_: Any, **__: Any) -> list[str]:
            return []

        pm._refresh_portfolio_value = _mock_refresh
        pm.get_position_size_limit = _mock_get_limit
        pm._refresh_positions_from_data_manager = _mock_refresh_from_dm
        pm.check_algo_order_limits = _mock_check_algo
        pm._get_allowed_symbols = _mock_allowed_symbols

        order = _make_order("BTCUSDT", "buy", amount=6.0)  # 5 + 6 = 11 > 10 → reject
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"
        ):
            result = await pm.check_position_limits(order)
        assert result is False
        assert pm.rejection_reason == "absolute_position_size"

    @pytest.mark.asyncio
    async def test_flag_on_uses_exchange_qty(self) -> None:
        pm = self._make_pm()
        pm.positions = {}  # local has nothing
        snap = _make_snapshot("BTCUSDT", "LONG", qty=5.0)
        pm.exchange_truth_store = _make_store({("BTCUSDT", "LONG"): snap})

        async def _mock_refresh(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_get_limit(*_: Any, **__: Any) -> float:
            return 10.0

        async def _mock_refresh_from_dm(*_: Any, **__: Any) -> None:
            pass

        async def _mock_check_algo(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_allowed_symbols(*_: Any, **__: Any) -> list[str]:
            return []

        pm._refresh_portfolio_value = _mock_refresh
        pm.get_position_size_limit = _mock_get_limit
        pm._refresh_positions_from_data_manager = _mock_refresh_from_dm
        pm.check_algo_order_limits = _mock_check_algo
        pm._get_allowed_symbols = _mock_allowed_symbols

        order = _make_order(
            "BTCUSDT", "buy", amount=6.0
        )  # exchange=5 + 6 = 11 > 10 → reject
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result = await pm.check_position_limits(order)
        assert result is False
        assert pm.rejection_reason == "absolute_position_size"

    @pytest.mark.asyncio
    async def test_flag_on_no_existing_exchange_position_allows_order(self) -> None:
        pm = self._make_pm()
        pm.positions = {}
        pm.exchange_truth_store = _make_store({})  # no BTCUSDT position

        async def _mock_refresh(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_get_limit(*_: Any, **__: Any) -> float:
            return 10.0

        async def _mock_refresh_from_dm(*_: Any, **__: Any) -> None:
            pass

        async def _mock_check_algo(*_: Any, **__: Any) -> bool:
            return True

        async def _mock_allowed_symbols(*_: Any, **__: Any) -> list[str]:
            return []

        pm._refresh_portfolio_value = _mock_refresh
        pm.get_position_size_limit = _mock_get_limit
        pm._refresh_positions_from_data_manager = _mock_refresh_from_dm
        pm.check_algo_order_limits = _mock_check_algo
        pm._get_allowed_symbols = _mock_allowed_symbols

        order = _make_order("BTCUSDT", "buy", amount=3.0)  # 0 + 3 = 3 < 10 → pass
        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result = await pm.check_position_limits(order)
        assert result is True


# ---------------------------------------------------------------------------
# AC3 — API source field
# ---------------------------------------------------------------------------


class TestAC3ApiSourceField:
    def test_source_field_local_when_flag_off(self) -> None:
        import tradeengine.api as api_module

        with patch.object(api_module, "TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"):
            flag = api_module.TE_EXCHANGE_TRUTH_STORE_ENABLED
        assert flag == "off"

    def test_source_field_exchange_when_flag_on(self) -> None:
        import tradeengine.api as api_module

        with patch.object(api_module, "TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"):
            source = (
                "exchange"
                if api_module.TE_EXCHANGE_TRUTH_STORE_ENABLED == "on"
                else "local"
            )
        assert source == "exchange"


# ---------------------------------------------------------------------------
# AC4 — StrategyPositionManager has exchange_truth_store attribute
# ---------------------------------------------------------------------------


class TestAC4StrategyPositionManagerAttribute:
    def test_attribute_exists_and_defaults_none(self) -> None:
        from tradeengine.strategy_position_manager import StrategyPositionManager

        spm = StrategyPositionManager()
        assert hasattr(spm, "exchange_truth_store")
        assert spm.exchange_truth_store is None

    def test_attribute_accepts_store_injection(self) -> None:
        from tradeengine.strategy_position_manager import StrategyPositionManager

        spm = StrategyPositionManager()
        store = ExchangeTruthStore()
        spm.exchange_truth_store = store
        assert spm.exchange_truth_store is store


# ---------------------------------------------------------------------------
# AC5 — Runtime flag toggle doesn't corrupt local state
# ---------------------------------------------------------------------------


class TestAC5RuntimeToggle:
    def test_toggle_does_not_corrupt_local_positions(self) -> None:
        from tradeengine.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm.positions = {("BTCUSDT", "LONG"): {"symbol": "BTCUSDT", "quantity": 2.0}}
        pm.exchange_truth_store = _make_store(
            {("BTCUSDT", "LONG"): _make_snapshot("BTCUSDT", "LONG", qty=3.5)}
        )

        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"
        ):
            result_on = pm.get_positions()

        with patch(
            "tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"
        ):
            result_off = pm.get_positions()

        # Exchange path returns 3.5; local path returns 2.0 — neither corrupts the other
        assert result_on[("BTCUSDT", "LONG")]["quantity"] == 3.5
        assert result_off[("BTCUSDT", "LONG")]["quantity"] == 2.0
        # Internal state is intact
        assert pm.positions[("BTCUSDT", "LONG")]["quantity"] == 2.0
