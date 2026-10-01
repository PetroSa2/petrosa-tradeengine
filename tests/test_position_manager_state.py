from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.constants import UTC
from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot
from tradeengine.position_manager import PositionManager


@pytest.fixture
def manager():
    m = PositionManager()
    m.total_portfolio_value = 10000.0
    return m


def _make_store(positions: dict) -> ExchangeTruthStore:
    store = ExchangeTruthStore()
    store._positions = dict(positions)
    store._is_ready = True
    return store


def test_get_cio_portfolio_summary_basic(manager):
    # Mock positions
    manager.positions = {
        ("BTCUSDT", "LONG"): {
            "quantity": 0.1,
            "avg_price": 40000.0,
            "symbol": "BTCUSDT",
        },
        ("ETHUSDT", "SHORT"): {
            "quantity": -1.0,
            "avg_price": 2000.0,
            "symbol": "ETHUSDT",
        },
    }

    summary = manager.get_cio_portfolio_summary("BTCUSDT")

    # Total exposure = abs(0.1 * 40000) + abs(-1.0 * 2000) = 4000 + 2000 = 6000
    # Gross exposure = 6000 / 10000 = 0.6
    # Same asset pct (BTCUSDT) = 4000 / 10000 = 0.4

    assert summary["gross_exposure"] == 0.6
    assert summary["same_asset_pct"] == 0.4
    assert summary["open_positions_count"] == 2


def test_get_cio_portfolio_summary_zero_portfolio(manager):
    manager.total_portfolio_value = 0.0
    summary = manager.get_cio_portfolio_summary("BTCUSDT")
    assert summary["open_positions_count"] == 0
    assert summary["gross_exposure"] == 0.0


def test_get_cio_portfolio_summary_empty(manager):
    manager.positions = {}
    summary = manager.get_cio_portfolio_summary("BTCUSDT")
    assert summary["open_positions_count"] == 0
    assert summary["gross_exposure"] == 0.0
    assert summary["same_asset_pct"] == 0.0


# ---------------------------------------------------------------------------
# #587 — /state open_positions_count must match exchange-authoritative
# /positions (i.e. get_positions()) rather than the raw local journal.
# ---------------------------------------------------------------------------


def test_get_cio_portfolio_summary_matches_get_positions_when_exchange_truth_on(
    manager,
):
    """Regression for #587: seed a known exchange position count (1) while the
    raw local journal has a much larger, stale count (13) — /state's
    open_positions_count must report the exchange-authoritative number,
    exactly matching what get_positions() (and therefore /positions) reports.
    """
    # Raw local audit journal: 13 stale entries never pruned on close.
    manager.positions = {
        (f"SYM{i}USDT", "LONG"): {
            "symbol": f"SYM{i}USDT",
            "quantity": 1.0,
            "avg_price": 100.0,
        }
        for i in range(13)
    }
    # Exchange truth: exactly 1 real open position (LTCUSDT).
    manager.exchange_truth_store = _make_store(
        {
            ("LTCUSDT", "LONG"): PositionSnapshot(
                symbol="LTCUSDT",
                side="LONG",
                quantity=2.0,
                entry_price=80.0,
                unrealized_pnl=0.0,
                updated_at=datetime.now(UTC),
            )
        }
    )

    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"):
        summary = manager.get_cio_portfolio_summary("LTCUSDT")
        positions = manager.get_positions()

    assert summary["open_positions_count"] == 1
    assert summary["open_positions_count"] == len(positions)
    # Raw journal is untouched — only the read path changed.
    assert len(manager.positions) == 13


def test_get_cio_portfolio_summary_flag_off_still_uses_local_journal(manager):
    """flag=off must preserve pre-#587 behaviour: count comes from the local
    journal (get_positions() falls back to self.positions when the store is
    disabled), so no exchange-truth-store dependency is introduced for
    deployments that haven't enabled the flag."""
    manager.positions = {
        ("BTCUSDT", "LONG"): {
            "symbol": "BTCUSDT",
            "quantity": 0.1,
            "avg_price": 40000.0,
        },
        ("ETHUSDT", "SHORT"): {
            "symbol": "ETHUSDT",
            "quantity": -1.0,
            "avg_price": 2000.0,
        },
    }
    manager.exchange_truth_store = None

    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"):
        summary = manager.get_cio_portfolio_summary("BTCUSDT")

    assert summary["open_positions_count"] == 2


@pytest.mark.asyncio
async def test_capital_base_defaults_to_available_and_exposes_equity():
    exchange = MagicMock()
    exchange.get_account_info = AsyncMock(
        return_value={
            "available_balance": 5572.12,
            "total_wallet_balance": 10098.60,
            "total_margin_balance": 10098.30,
        }
    )
    manager = PositionManager(exchange=exchange)
    manager.portfolio_value_last_update = None

    assert await manager._refresh_portfolio_value() is True
    assert manager.total_portfolio_value == 5572.12
    assert manager.available_margin == 5572.12
    assert manager.equity == 10098.30
    assert manager.capital_base == "available"


@pytest.mark.asyncio
async def test_equity_capital_base_uses_margin_then_wallet_fallback(caplog):
    exchange = MagicMock()
    exchange.get_account_info = AsyncMock(
        side_effect=[
            {
                "available_balance": 2000.0,
                "total_wallet_balance": 10000.0,
                "total_margin_balance": 10500.0,
            },
            {"available_balance": 2000.0, "total_wallet_balance": 10000.0},
        ]
    )
    manager = PositionManager(exchange=exchange)
    manager.settings.te_capital_base = "equity"

    assert await manager._refresh_portfolio_value() is True
    assert manager.total_portfolio_value == 10500.0
    manager.portfolio_value_last_update = None
    assert await manager._refresh_portfolio_value() is True
    assert manager.total_portfolio_value == 10000.0
    assert "total_margin_balance absent" in caplog.text


def test_state_notional_fields_use_signed_position_sides(manager):
    manager.available_margin = 5572.0
    manager.equity = 10098.0
    manager.capital_base = "equity"
    manager.positions = {
        ("BTCUSDT", "LONG"): {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.0144,
            "mark_price": 60000.0,
        },
        ("BTCUSDT", "SHORT"): {
            "symbol": "BTCUSDT",
            "position_side": "SHORT",
            "quantity": 0.0024,
            "mark_price": 60000.0,
        },
    }

    summary = manager.get_portfolio_summary()

    assert summary["gross_notional"] == pytest.approx(1008.0)
    assert summary["net_notional"] == pytest.approx(720.0)
    assert summary["capital_base"] == "equity"
    assert summary["available_margin"] == 5572.0
