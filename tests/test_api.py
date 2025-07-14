from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import Signal
from tradeengine.api import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
    )


@pytest.fixture
def sample_order() -> TradeOrder:
    return TradeOrder(
        symbol="BTCUSDT",
        order_type=OrderType.MARKET,
        side=OrderSide.BUY,
        quantity=0.1,
        price=45000.0,
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        position_size_pct=0.1,
    )


def test_health_endpoint(client: TestClient) -> None:
    """Test health endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded", "unhealthy"]


def test_ready_endpoint(client: TestClient) -> None:
    """Test ready endpoint"""
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_live_endpoint(client: TestClient) -> None:
    """Test live endpoint"""
    response = client.get("/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_trade_endpoint_single_signal(
    client: TestClient, sample_signal: Signal
) -> None:
    """Test trade endpoint with single signal"""
    with patch("tradeengine.api.dispatcher") as mock_dispatcher:
        mock_dispatcher.process_signal.return_value = {
            "status": "executed",
            "order_id": "test-order-1",
            "message": "Order executed successfully",
        }

        response = client.post("/trade", json=sample_signal.dict())
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "executed"


@pytest.mark.asyncio
async def test_trade_endpoint_multiple_signals(client: TestClient) -> None:
    """Test trade endpoint with multiple signals"""
    signals = [
        Signal(
            strategy_id="test-strategy-1",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            strength="medium",
            timeframe="1h",
            current_price=45000.0,
        ),
        Signal(
            strategy_id="test-strategy-2",
            symbol="ETHUSDT",
            action="sell",
            confidence=0.7,
            strength="medium",
            timeframe="1h",
            current_price=3000.0,
        ),
    ]

    with patch("tradeengine.api.dispatcher") as mock_dispatcher:
        mock_dispatcher.process_signals.return_value = [
            {"status": "executed", "order_id": "test-order-1"},
            {"status": "rejected", "order_id": "test-order-2"},
        ]

        response = client.post("/trade/batch", json=[s.dict() for s in signals])
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2


def test_account_endpoint(client: TestClient) -> None:
    """Test account endpoint"""
    response = client.get("/account")
    assert response.status_code == 200
    data = response.json()
    assert "balance" in data
    assert "positions" in data


def test_price_endpoint(client: TestClient) -> None:
    """Test price endpoint"""
    response = client.get("/price/BTCUSDT")
    assert response.status_code == 200
    data = response.json()
    assert "price" in data
    assert isinstance(data["price"], int | float)


def test_order_endpoint(client: TestClient) -> None:
    """Test order endpoint"""
    response = client.get("/order/BTCUSDT/test-order-1")
    assert response.status_code == 200
    data = response.json()
    assert "order_id" in data


def test_cancel_order_endpoint(client: TestClient) -> None:
    """Test cancel order endpoint"""
    response = client.delete("/order/BTCUSDT/test-order-1")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["cancelled", "not_found"]


def test_metrics_endpoint(client: TestClient) -> None:
    """Test metrics endpoint"""
    response = client.get("/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "orders" in data
    assert "positions" in data
