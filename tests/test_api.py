from datetime import datetime

from fastapi.testclient import TestClient

from tradeengine.api import app

client = TestClient(app)


def test_root_endpoint():
    """Test the root endpoint"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Petrosa Trading Engine"
    assert data["status"] == "running"


def test_health_endpoint():
    """Test the health endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]


def test_metrics_endpoint():
    """Test the metrics endpoint"""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_trade_endpoint_success():
    """Test successful trade signal processing"""
    signal_data = {
        "strategy_id": "test_strategy",
        "symbol": "BTCUSDT",
        "action": "buy",
        "confidence": 0.8,
        "strength": "strong",
        "timeframe": "1h",
        "current_price": 45000.0,
        "order_type": "market",
        "time_in_force": "GTC",
        "strategy_mode": "deterministic",
        "timestamp": datetime.now().isoformat(),
        "meta": {"simulate": True},
    }

    response = client.post("/trade/signal", json=signal_data)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Signal processed successfully"
    assert data["signal_id"] == "test_strategy"
    assert "result" in data


def test_trade_endpoint_invalid_confidence():
    """Test trade endpoint with invalid confidence"""
    signal_data = {
        "strategy_id": "test_strategy",
        "symbol": "BTCUSDT",
        "action": "buy",
        "confidence": 1.5,  # Invalid - greater than 1
        "strength": "strong",
        "timeframe": "1h",
        "current_price": 45000.0,
        "order_type": "market",
        "time_in_force": "GTC",
        "strategy_mode": "deterministic",
        "timestamp": datetime.now().isoformat(),
        "meta": {"simulate": True},
    }

    response = client.post("/trade/signal", json=signal_data)
    assert response.status_code == 422  # Pydantic validation error


def test_trade_endpoint_invalid_action():
    """Test trade endpoint with invalid action"""
    signal_data = {
        "strategy_id": "test_strategy",
        "symbol": "BTCUSDT",
        "action": "invalid_action",
        "price": 45000.0,
        "confidence": 0.8,
        "timestamp": datetime.now().isoformat(),
        "meta": {"simulate": True},
    }

    response = client.post("/trade", json=signal_data)
    assert response.status_code == 422  # Pydantic validation error


def test_version_endpoint():
    """Test the version endpoint"""
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Petrosa Trading Engine"
    assert data["version"] == "0.1.0"
    assert "description" in data
    assert "build_date" in data
    assert "python_version" in data
    assert "api_version" in data


def test_openapi_specs_endpoint():
    """Test the OpenAPI specifications endpoint"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["openapi"] == "3.1.0"
    assert data["info"]["title"] == "Petrosa Trading Engine API"
    assert data["info"]["version"] == "1.1.0"
    assert "paths" in data
    assert "components" in data
