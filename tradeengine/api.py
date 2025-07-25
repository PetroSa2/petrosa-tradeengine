import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from contracts.order import TradeOrder
from contracts.signal import Signal
from shared.audit import audit_logger
from shared.config import Settings
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange.binance import BinanceFuturesExchange
from tradeengine.exchange.simulator import SimulatorExchange

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Petrosa Trading Engine...")

    try:
        # Validate MongoDB configuration first - fail catastrophically if not configured
        logger.info("Validating MongoDB configuration...")
        from shared.constants import validate_mongodb_config

        validate_mongodb_config()
        logger.info("✅ MongoDB configuration validated successfully")

        # Initialize audit logger
        if audit_logger.enabled and audit_logger.connected:
            logger.info("Audit logging enabled and connected")
        elif audit_logger.enabled:
            logger.warning("Audit logging enabled but not connected")
        else:
            logger.info("Audit logging disabled")

        # Initialize exchanges
        logger.info("Initializing Binance exchange...")
        await binance_exchange.initialize()
        logger.info("Initializing simulator exchange...")
        await simulator_exchange.initialize()

        # Initialize dispatcher
        logger.info("Initializing dispatcher...")
        await dispatcher.initialize()

        logger.info("Trading engine startup completed successfully")
    except Exception as e:
        logger.error(f"CRITICAL: Startup failed - {e}")
        logger.error("Service will exit due to critical configuration error")
        raise

    yield

    # Shutdown
    logger.info("Shutting down Petrosa Trading Engine...")
    try:
        await binance_exchange.close()
        await simulator_exchange.close()
        await dispatcher.close()
        logger.info("Trading engine shutdown completed")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")
    logger.info("Petrosa Trading Engine shut down complete")


# Initialize FastAPI app
app = FastAPI(
    title="Petrosa Trading Engine API",
    description=(
        "Advanced cryptocurrency trading engine with multi-strategy signal aggregation"
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# Initialize components
settings = Settings()
dispatcher = Dispatcher()
binance_exchange = BinanceFuturesExchange()
simulator_exchange = SimulatorExchange()

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Health check response model"""

    status: str
    version: str
    timestamp: str
    components: dict[str, Any]


class AccountResponse(BaseModel):
    """Account information response model"""

    account_type: str
    balances: dict[str, Any]
    total_balance_usdt: float
    positions: dict[str, Any]
    pnl: dict[str, Any]
    risk_metrics: dict[str, Any]


class TradeRequest(BaseModel):
    """Enhanced trade request model supporting all order types"""

    signals: list[Signal]
    conflict_resolution: str = "strongest_wins"
    timeframe_resolution: str = "higher_timeframe_wins"
    risk_management: bool = True
    audit_logging: bool = True


class TradeResponse(BaseModel):
    """Trade response model"""

    status: str
    orders: list[dict[str, Any]]
    signals_processed: int
    conflicts_resolved: int
    audit_logs: list[dict[str, Any]]


@app.get("/")
async def root() -> dict[str, Any]:
    """Health check endpoint"""
    return {
        "service": "Petrosa Trading Engine",
        "version": "0.1.0",
        "status": "running",
        "environment": settings.environment,
        "features": [
            "Multi-strategy signal aggregation",
            "Deterministic rule-based processing",
            "ML light model support",
            "LLM reasoning support",
            "Advanced order types",
            "Risk management",
            "Position tracking",
        ],
    }


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Comprehensive health check endpoint with distributed state info"""
    try:
        # Validate MongoDB configuration in health check
        from shared.constants import validate_mongodb_config

        validate_mongodb_config()

        # Check component health
        components = {
            "dispatcher": await dispatcher.health_check(),
            "binance_exchange": await binance_exchange.health_check(),
            "simulator_exchange": await simulator_exchange.health_check(),
            "audit_logger": {
                "enabled": audit_logger.enabled,
                "connected": audit_logger.connected,
            },
            "mongodb_config": {
                "status": "healthy",
                "configured": True,
            },
        }

        # Determine overall status
        all_healthy = all(
            comp.get("status", "unknown") == "healthy"
            for comp in components.values()
            if isinstance(comp, dict)
        )

        return HealthResponse(
            status="healthy" if all_healthy else "degraded",
            version="1.1.0",
            timestamp=datetime.utcnow().isoformat(),
            components=components,
        )
    except Exception as e:
        logger.error(f"Health check error: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")


@app.get("/distributed-state")
async def get_distributed_state() -> dict[str, Any]:
    """Get distributed state information"""
    try:
        from shared.distributed_lock import distributed_lock_manager

        leader_info = await distributed_lock_manager.get_leader_info()
        position_manager_health = await dispatcher.position_manager.health_check()

        return {
            "status": "success",
            "data": {
                "leader_info": leader_info,
                "position_manager": position_manager_health,
                "pod_id": distributed_lock_manager.pod_id,
                "is_leader": distributed_lock_manager.is_leader,
                "database_connected": position_manager_health.get(
                    "database_connected", False
                ),
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error(f"Error getting distributed state: {err}")
        raise HTTPException(status_code=500, detail="Failed to get distributed state")


@app.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness probe for Kubernetes"""
    try:
        # Validate MongoDB configuration first
        from shared.constants import validate_mongodb_config

        validate_mongodb_config()

        # Check if core components are ready
        dispatcher_ready = await dispatcher.health_check()
        binance_ready = await binance_exchange.health_check()
        simulator_ready = await simulator_exchange.health_check()

        if (
            dispatcher_ready.get("status") == "healthy"
            and binance_ready.get("status") == "healthy"
            and simulator_ready.get("status") == "healthy"
        ):
            return {"status": "ready"}
        else:
            raise HTTPException(status_code=503, detail="Components not ready")
    except Exception as e:
        logger.error(f"Readiness check error: {e}")
        raise HTTPException(status_code=503, detail=f"Not ready: {e}")


@app.get("/live")
async def liveness_check() -> dict[str, Any]:
    """Liveness probe for Kubernetes"""
    try:
        # Simple liveness check
        return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        logger.error(f"Liveness check error: {e}")
        raise HTTPException(status_code=500, detail=f"Not alive: {e}")


@app.post("/trade", response_model=TradeResponse)
async def process_trade(
    request: TradeRequest, background_tasks: BackgroundTasks
) -> TradeResponse:
    """Process trading signals with distributed state management"""
    try:
        # Get distributed state info
        from shared.distributed_lock import distributed_lock_manager

        leader_info = await distributed_lock_manager.get_leader_info()

        # Process signals
        orders = []
        signals_processed = 0
        conflicts_resolved = 0
        audit_logs = []

        for signal in request.signals:
            try:
                # Process signal with distributed consensus
                result = await dispatcher.dispatch(signal)

                if result.get("status") == "executed":
                    signals_processed += 1
                    if result.get("conflicts_resolved"):
                        conflicts_resolved += result["conflicts_resolved"]

                    # Add order info
                    if "execution_result" in result:
                        orders.append(
                            {
                                "signal": signal.model_dump(),
                                "result": result["execution_result"],
                                "distributed_state": {
                                    "pod_id": distributed_lock_manager.pod_id,
                                    "is_leader": distributed_lock_manager.is_leader,
                                    "leader_info": leader_info,
                                },
                            }
                        )

                # Log for audit
                if request.audit_logging and audit_logger.enabled:
                    audit_logs.append(
                        {
                            "signal": signal.model_dump(),
                            "result": result,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )

            except Exception as e:
                logger.error(f"Error processing signal: {e}")
                orders.append(
                    {
                        "signal": signal.model_dump(),
                        "error": str(e),
                        "distributed_state": {
                            "pod_id": distributed_lock_manager.pod_id,
                            "is_leader": distributed_lock_manager.is_leader,
                            "leader_info": leader_info,
                        },
                    }
                )

        return TradeResponse(
            status="success",
            orders=orders,
            signals_processed=signals_processed,
            conflicts_resolved=conflicts_resolved,
            audit_logs=audit_logs,
        )

    except Exception as e:
        logger.error(f"Trade processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Trade processing failed: {e}")


@app.post("/trade/signal")
async def process_single_signal(signal: Signal) -> dict[str, Any]:
    """Process a single trading signal"""
    try:
        result = await dispatcher.dispatch(signal)
        return {
            "status": "success",
            "signal": signal.model_dump(),
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Single signal processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Signal processing failed: {e}")


@app.post("/order")
async def place_advanced_order(order: TradeOrder) -> dict[str, Any]:
    """Place an advanced order with specific parameters"""
    try:
        # Process the order through the dispatcher
        result = await dispatcher.execute_order(order)
        return {
            "status": "success",
            "order": order.model_dump(),
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Advanced order placement error: {e}")
        raise HTTPException(status_code=500, detail=f"Order placement failed: {e}")


@app.get("/account", response_model=AccountResponse)
async def get_account_info() -> AccountResponse:
    """Get comprehensive account information"""
    try:
        # Get account info from both exchanges
        binance_account = await binance_exchange.get_account_info()
        simulator_account = await simulator_exchange.get_account_info()

        # Combine account information
        combined_balances = {}
        combined_positions = {}
        combined_pnl = {}

        # Merge binance data
        if binance_account:
            # Binance returns balances as a list, convert to dict by asset
            binance_balances = binance_account.get("balances", [])
            if isinstance(binance_balances, list):
                for balance in binance_balances:
                    if isinstance(balance, dict) and "asset" in balance:
                        combined_balances[balance["asset"]] = balance
            elif isinstance(binance_balances, dict):
                combined_balances.update(binance_balances)
            # else: ignore
            combined_positions.update(binance_account.get("positions", {}))
            combined_pnl.update(binance_account.get("pnl", {}))

        # Merge simulator data
        if simulator_account:
            simulator_balances = simulator_account.get("balances", {})
            if isinstance(simulator_balances, dict):
                combined_balances.update(simulator_balances)
            combined_positions.update(simulator_account.get("positions", {}))
            combined_pnl.update(simulator_account.get("pnl", {}))

        # Calculate total USDT balance
        total_balance_usdt = 0.0
        for balance in combined_balances.values():
            if isinstance(balance, dict) and balance.get("asset") == "USDT":
                try:
                    free = float(balance.get("free", 0))
                    locked = float(balance.get("locked", 0))
                    total_balance_usdt += free + locked
                except (ValueError, TypeError):
                    continue

        # Calculate risk metrics
        total_exposure = 0.0
        for pos in combined_positions.values():
            if isinstance(pos, dict):
                try:
                    notional = float(pos.get("notional", 0))
                    total_exposure += notional
                except (ValueError, TypeError):
                    continue

        total_pnl = 0.0
        for pnl in combined_pnl.values():
            if isinstance(pnl, dict):
                try:
                    realized = float(pnl.get("realized", 0))
                    total_pnl += realized
                except (ValueError, TypeError):
                    continue

        risk_metrics = {
            "total_exposure": total_exposure,
            "open_positions": len(combined_positions),
            "total_pnl": total_pnl,
        }

        return AccountResponse(
            account_type="combined",
            balances=combined_balances,
            total_balance_usdt=total_balance_usdt,
            positions=combined_positions,
            pnl=combined_pnl,
            risk_metrics=risk_metrics,
        )

    except Exception as e:
        logger.error(f"Account info error: {e}")
        logger.error(f"Exception type: {type(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get account info: {e}")


@app.get("/price/{symbol}")
async def get_price(symbol: str) -> dict[str, Any]:
    """Get current price for a symbol"""
    try:
        # Try binance first, then simulator
        try:
            price = await binance_exchange.get_price(symbol)
            source = "binance"
        except Exception:
            price = await simulator_exchange.get_price(symbol)
            source = "simulator"

        return {
            "symbol": symbol,
            "price": price,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Price fetch error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get price: {e}")


@app.delete("/order/{symbol}/{order_id}")
async def cancel_order(symbol: str, order_id: str) -> dict[str, Any]:
    """Cancel an order by symbol and order ID"""
    try:
        # Try to cancel on both exchanges
        result = None
        source = None

        try:
            result = await binance_exchange.cancel_order(symbol, int(order_id))
            source = "binance"
        except Exception:
            result = await simulator_exchange.cancel_order(symbol, order_id)
            source = "simulator"

        return {
            "status": "success",
            "symbol": symbol,
            "order_id": order_id,
            "result": result,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Order cancellation error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {e}")


@app.get("/order/{symbol}/{order_id}")
async def get_order_status(symbol: str, order_id: str) -> dict[str, Any]:
    """Get order status by symbol and order ID"""
    try:
        # Try to get status from both exchanges
        result = None
        source = None

        try:
            result = await binance_exchange.get_order_status(symbol, int(order_id))
            source = "binance"
        except Exception:
            result = await simulator_exchange.get_order_status(symbol, order_id)
            source = "simulator"

        return {
            "status": "success",
            "symbol": symbol,
            "order_id": order_id,
            "result": result,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Order status error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get order status: {e}")


@app.get("/signals/summary")
async def get_signal_summary() -> dict[str, Any]:
    """Get summary of signal processing statistics"""
    try:
        # Get signal summary from dispatcher
        summary = dispatcher.get_signal_summary()
        return {
            "status": "success",
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Signal summary error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get signal summary: {e}"
        )


@app.post("/signals/strategy/{strategy_id}/weight")
async def set_strategy_weight(strategy_id: str, weight: float) -> dict[str, Any]:
    """Set weight for a specific strategy"""
    try:
        # Set strategy weight in dispatcher
        dispatcher.set_strategy_weight(strategy_id, weight)
        return {
            "status": "success",
            "strategy_id": strategy_id,
            "weight": weight,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Strategy weight error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to set strategy weight: {e}"
        )


@app.get("/signals/active")
async def get_active_signals() -> dict[str, Any]:
    """Get currently active signals"""
    try:
        # Get active signals from dispatcher
        if hasattr(dispatcher, "get_active_signals"):
            signals = dispatcher.get_active_signals()
        else:
            signals = []
        return {
            "status": "success",
            "signals": signals,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Active signals error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get active signals: {e}"
        )


@app.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Get all positions"""
    try:
        # Get positions from position manager
        positions = dispatcher.position_manager.get_positions()
        return {
            "status": "success",
            "positions": positions,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Positions error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get positions: {e}")


@app.get("/positions/{symbol}")
async def get_position(symbol: str) -> dict[str, Any]:
    """Get position for a specific symbol"""
    try:
        # Get position from position manager
        position = dispatcher.position_manager.get_position(symbol)
        return {
            "status": "success",
            "symbol": symbol,
            "position": position,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Position error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get position: {e}")


@app.get("/orders")
async def get_orders() -> dict[str, Any]:
    """Get all orders"""
    try:
        # Get orders from both exchanges
        binance_orders = await binance_exchange.get_order_status("BTCUSDT", 0)
        simulator_orders = await simulator_exchange.get_order_status("BTCUSDT", "0")

        # Combine orders
        all_orders = {
            "binance": binance_orders,
            "simulator": simulator_orders,
        }

        return {
            "status": "success",
            "orders": all_orders,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Orders error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get orders: {e}")


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict[str, Any]:
    """Get order by ID"""
    try:
        # Try to get order from both exchanges
        result = None
        source = None

        try:
            result = await binance_exchange.get_order_status("BTCUSDT", int(order_id))
            source = "binance"
        except Exception:
            result = await simulator_exchange.get_order_status("BTCUSDT", order_id)
            source = "simulator"

        return {
            "status": "success",
            "order_id": order_id,
            "result": result,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Order error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get order: {e}")


@app.delete("/orders/{order_id}")
async def cancel_order_by_id(order_id: str) -> dict[str, Any]:
    """Cancel order by ID"""
    try:
        # Try to cancel on both exchanges
        result = None
        source = None

        try:
            result = await binance_exchange.cancel_order("BTCUSDT", int(order_id))
            source = "binance"
        except Exception:
            result = await simulator_exchange.cancel_order("BTCUSDT", order_id)
            source = "simulator"

        return {
            "status": "success",
            "order_id": order_id,
            "result": result,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Order cancellation error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {e}")


@app.get("/version")
async def get_version() -> dict[str, Any]:
    """Get service version information"""
    return {
        "service": "Petrosa Trading Engine",
        "version": "1.1.0",
        "build_date": "2024-01-15",
        "environment": settings.environment,
        "features": [
            "Multi-strategy signal aggregation",
            "Deterministic rule-based processing",
            "ML light model support",
            "LLM reasoning support",
            "Advanced order types",
            "Risk management",
            "Position tracking",
            "Distributed state management",
        ],
    }


@app.get("/openapi.json")
async def get_openapi_specs() -> Any:
    """Get OpenAPI specification"""
    return app.openapi()


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Get Prometheus metrics"""
    from prometheus_client import generate_latest

    return PlainTextResponse(generate_latest())


@app.get("/docs")
async def get_documentation() -> dict[str, Any]:
    """Get API documentation information"""
    return {
        "title": "Petrosa Trading Engine API",
        "version": "1.1.0",
        "description": (
            "Signal-driven cryptocurrency trading engine with distributed state "
            "management"
        ),
        "endpoints": [
            "/health - Health check",
            "/ready - Readiness probe",
            "/live - Liveness probe",
            "/trade - Process trading signals",
            "/account - Get account information",
            "/price/{symbol} - Get current price",
            "/positions - Get all positions",
            "/orders - Get all orders",
            "/docs - Interactive documentation",
            "/openapi.json - OpenAPI specification",
            "/metrics - Prometheus metrics",
        ],
        "documentation_url": "/docs",
        "openapi_url": "/openapi.json",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "tradeengine.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
