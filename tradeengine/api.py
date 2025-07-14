import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from shared.audit import audit_logger
from shared.config import Settings
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange.binance import BinanceExchange
from tradeengine.exchange.simulator import SimulatorExchange

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Petrosa Trading Engine...")

    try:
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
        logger.error(f"Startup error: {e}")
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
binance_exchange = BinanceExchange()
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
    """Comprehensive health check endpoint"""
    try:
        # Check component health
        components = {
            "dispatcher": await dispatcher.health_check(),
            "binance_exchange": await binance_exchange.health_check(),
            "simulator_exchange": await simulator_exchange.health_check(),
            "audit_logger": {
                "enabled": audit_logger.enabled,
                "connected": audit_logger.connected,
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


@app.get("/ready")
async def readiness_check() -> dict[str, Any]:
    """Readiness probe for Kubernetes"""
    try:
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
    """Process trading signals with advanced conflict resolution"""
    try:
        # Validate request
        if not request.signals:
            raise HTTPException(status_code=400, detail="No signals provided")

        # Log trade request
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_signal(
                {
                    "signals_count": len(request.signals),
                    "conflict_resolution": request.conflict_resolution,
                    "timeframe_resolution": request.timeframe_resolution,
                    "risk_management": request.risk_management,
                }
            )

        # Process signals through dispatcher
        results = []
        orders = []
        conflicts_resolved = 0

        for signal in request.signals:
            try:
                result = await dispatcher.process_signal(
                    signal,
                    conflict_resolution=request.conflict_resolution,
                    timeframe_resolution=request.timeframe_resolution,
                    risk_management=request.risk_management,
                )

                results.append(result)

                if result.get("status") == "success":
                    # Create order from signal
                    order = TradeOrder(
                        order_id=(
                            f"order_{signal.strategy_id}_{datetime.utcnow().timestamp()}"
                        ),
                        symbol=signal.symbol,
                        type=signal.order_type.value,
                        side=signal.action,
                        amount=signal.position_size_pct or 0.001,
                        target_price=signal.current_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        conditional_price=signal.conditional_price,
                        conditional_direction=signal.conditional_direction,
                        conditional_timeout=signal.conditional_timeout,
                        iceberg_quantity=signal.iceberg_quantity,
                        client_order_id=signal.client_order_id,
                        time_in_force=signal.time_in_force.value,
                        status=OrderStatus.PENDING,
                        filled_amount=0.0,
                        average_price=0.0,
                        created_at=signal.timestamp,
                        updated_at=signal.timestamp,
                        position_size_pct=signal.position_size_pct or 0.0,
                        simulate=True,  # Default to simulation for safety
                    )

                    # Execute order
                    order_result = await dispatcher.execute_order(order)
                    orders.append(order_result)

                    # Log order execution
                    if audit_logger.enabled and audit_logger.connected:
                        audit_logger.log_order(
                            {
                                "order": order.model_dump(),
                                "result": order_result,
                                "signal": signal.model_dump(),
                            }
                        )

                elif result.get("status") == "conflict_resolved":
                    conflicts_resolved += 1

            except Exception as e:
                logger.error(f"Error processing signal: {e}")
                if audit_logger.enabled and audit_logger.connected:
                    audit_logger.log_error(
                        {
                            "error": str(e),
                            "signal": signal.model_dump(),
                            "endpoint": "/trade",
                        }
                    )

        # Prepare response
        response = TradeResponse(
            status="completed",
            orders=[order for order in orders if order is not None],
            signals_processed=len(request.signals),
            conflicts_resolved=conflicts_resolved,
            audit_logs=results,
        )

        # Log trade completion
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_trade(
                {
                    "request": request.model_dump(),
                    "response": response.model_dump(),
                    "orders_count": len(orders),
                    "conflicts_resolved": conflicts_resolved,
                }
            )

        return response

    except Exception as e:
        logger.error(f"Trade processing error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error(
                {"error": str(e), "endpoint": "/trade", "request": request.model_dump()}
            )
        raise HTTPException(status_code=500, detail=f"Trade processing failed: {e}")


@app.post("/trade/signal")
async def process_single_signal(signal: Signal) -> dict[str, Any]:
    """Process a single trading signal (backward compatibility)"""
    try:
        # Log signal
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_signal(signal.model_dump())

        # Process signal through dispatcher
        result = await dispatcher.process_signal(signal)

        return {
            "message": "Signal processed successfully",
            "signal_id": signal.strategy_id,
            "result": result or {},
        }

    except Exception as e:
        logger.error(f"Signal processing error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error(
                {
                    "error": str(e),
                    "signal": signal.model_dump(),
                    "endpoint": "/trade/signal",
                }
            )
        raise HTTPException(status_code=500, detail=f"Signal processing failed: {e}")


@app.post("/order")
async def place_advanced_order(order: TradeOrder) -> dict:
    """
    Place an advanced order directly (bypassing signal processing)

    This endpoint allows direct order placement with full Binance parameter support.
    Use this for advanced trading strategies that need precise control over "
    "order parameters.
    """
    try:
        logger.info(f"Placing advanced order: {order.type} {order.side} {order.symbol}")

        # Execute the order directly
        result = await dispatcher.execute_order(order)

        return {
            "status": "success",
            "message": "Advanced order placed successfully",
            "order_id": result.get("order_id") if result else None,
            "result": result,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    except Exception as err:
        logger.error(f"Error placing advanced order: {err}")
        raise HTTPException(
            status_code=500, detail=f"Order placement error: {str(err)}"
        )


@app.get("/account", response_model=AccountResponse)
async def get_account_info() -> AccountResponse:
    """Get detailed account information"""
    try:
        # Get account info from both exchanges
        binance_account = await binance_exchange.get_account_info()
        simulator_account = await simulator_exchange.get_account_info()

        # Calculate total balance in USDT
        total_usdt_value = 0.0

        # Combine account information
        account_info = {
            "account_type": "hybrid",  # Both real and simulated
            "balances": {
                "binance": binance_account.get("balances", {}),
                "simulator": simulator_account.get("balances", {}),
            },
            "total_balance_usdt": total_usdt_value,
            "positions": {
                "binance": binance_account.get("positions", {}),
                "simulator": simulator_account.get("positions", {}),
            },
            "pnl": {
                "binance": binance_account.get("pnl", {}),
                "simulator": simulator_account.get("pnl", {}),
            },
            "risk_metrics": {
                "binance": binance_account.get("risk_metrics", {}),
                "simulator": simulator_account.get("risk_metrics", {}),
            },
        }

        # Log account access
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_account(account_info)

        # Ensure all values are dictionaries
        balances = account_info.get("balances")
        positions = account_info.get("positions")
        pnl = account_info.get("pnl")
        risk_metrics = account_info.get("risk_metrics")
        total_balance_usdt_raw = account_info.get("total_balance_usdt", 0.0)
        if isinstance(total_balance_usdt_raw, int | float):
            total_balance_usdt = float(total_balance_usdt_raw)
        else:
            try:
                total_balance_usdt = float(str(total_balance_usdt_raw))
            except Exception:
                total_balance_usdt = 0.0

        return AccountResponse(
            account_type=str(account_info.get("account_type", "unknown")),
            balances=dict(balances) if isinstance(balances, dict) else {},
            total_balance_usdt=total_balance_usdt,
            positions=dict(positions) if isinstance(positions, dict) else {},
            pnl=dict(pnl) if isinstance(pnl, dict) else {},
            risk_metrics=dict(risk_metrics) if isinstance(risk_metrics, dict) else {},
        )
    except Exception as e:
        logger.error(f"Account info error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error({"error": str(e), "endpoint": "/account"})
        raise HTTPException(status_code=500, detail=f"Failed to get account info: {e}")


@app.get("/price/{symbol}")
async def get_price(symbol: str) -> dict[str, Any]:
    """Get current price for a symbol"""
    try:
        # Get price from both exchanges
        binance_price = await binance_exchange.get_price(symbol)
        simulator_price = await simulator_exchange.get_price(symbol)

        return {
            "symbol": symbol,
            "binance_price": binance_price,
            "simulator_price": simulator_price,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Price check error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error(
                {"error": str(e), "symbol": symbol, "endpoint": "/price"}
            )
        raise HTTPException(status_code=500, detail=f"Failed to get price: {e}")


@app.delete("/order/{symbol}/{order_id}")
async def cancel_order(symbol: str, order_id: str) -> dict[str, Any]:
    """Cancel an existing order"""
    try:
        # Cancel order from both exchanges
        binance_result = await binance_exchange.cancel_order(
            symbol, int(order_id) if order_id.isdigit() else 0
        )
        simulator_result = await simulator_exchange.cancel_order(symbol, order_id)

        result = {
            "symbol": symbol,
            "order_id": order_id,
            "binance_result": binance_result,
            "simulator_result": simulator_result,
            "cancelled": binance_result.get("success")
            or simulator_result.get("success"),
        }

        # Log order cancellation
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_order(
                {
                    "action": "cancel",
                    "symbol": symbol,
                    "order_id": order_id,
                    "result": result,
                }
            )

        return result
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error(
                {
                    "error": str(e),
                    "symbol": symbol,
                    "order_id": order_id,
                    "endpoint": "/cancel_order",
                }
            )
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {e}")


@app.get("/order/{symbol}/{order_id}")
async def get_order_status(symbol: str, order_id: str) -> dict[str, Any]:
    """Get status of an existing order"""
    try:
        # Get order status from both exchanges
        binance_status = await binance_exchange.get_order_status(
            symbol, int(order_id) if order_id.isdigit() else 0
        )
        simulator_status = await simulator_exchange.get_order_status(symbol, order_id)

        return {
            "symbol": symbol,
            "order_id": order_id,
            "binance_status": binance_status,
            "simulator_status": simulator_status,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Order status error: {e}")
        if audit_logger.enabled and audit_logger.connected:
            audit_logger.log_error(
                {
                    "error": str(e),
                    "symbol": symbol,
                    "order_id": order_id,
                    "endpoint": "/order_status",
                }
            )
        raise HTTPException(status_code=500, detail=f"Failed to get order status: {e}")


@app.get("/signals/summary")
async def get_signal_summary() -> dict[str, Any]:
    """Get summary of signal processing and aggregation"""
    try:
        summary = dispatcher.get_signal_summary()
        return {
            "status": "success",
            "data": summary,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error(f"Error getting signal summary: {err}")
        raise HTTPException(status_code=500, detail="Failed to get signal summary")


@app.post("/signals/strategy/{strategy_id}/weight")
async def set_strategy_weight(strategy_id: str, weight: float) -> dict[str, Any]:
    """Set weight for a strategy in signal aggregation"""
    try:
        if weight < 0 or weight > 10:
            raise HTTPException(
                status_code=400, detail="Weight must be between 0 and 10"
            )

        dispatcher.set_strategy_weight(strategy_id, weight)

        return {
            "status": "success",
            "message": f"Strategy weight set for {strategy_id}",
            "strategy_id": strategy_id,
            "weight": weight,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error setting strategy weight: {err}")
        raise HTTPException(status_code=500, detail="Failed to set strategy weight")


@app.get("/signals/active")
async def get_active_signals() -> dict[str, Any]:
    """Get all active signals"""
    try:
        active_signals = []
        for signal in dispatcher.signal_aggregator.active_signals.values():
            active_signals.append(
                {
                    "strategy_id": signal.strategy_id,
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "confidence": signal.confidence,
                    "strength": signal.strength.value,
                    "strategy_mode": signal.strategy_mode.value,
                    "timestamp": signal.timestamp.isoformat(),
                    "order_type": signal.order_type.value,
                }
            )

        return {
            "status": "success",
            "data": {"active_signals": active_signals, "count": len(active_signals)},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error(f"Error getting active signals: {err}")
        raise HTTPException(status_code=500, detail="Failed to get active signals")


@app.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Get all current positions"""
    try:
        positions = dispatcher.get_positions()
        portfolio_summary = dispatcher.get_portfolio_summary()

        return {
            "status": "success",
            "data": {"positions": positions, "portfolio_summary": portfolio_summary},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error(f"Error getting positions: {err}")
        raise HTTPException(status_code=500, detail="Failed to get positions")


@app.get("/positions/{symbol}")
async def get_position(symbol: str) -> dict[str, Any]:
    """Get specific position"""
    try:
        position = dispatcher.get_position(symbol)

        if not position:
            raise HTTPException(
                status_code=404, detail=f"Position not found for {symbol}"
            )

        return {
            "status": "success",
            "data": position,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error getting position for {symbol}: {err}")
        raise HTTPException(status_code=500, detail="Failed to get position")


@app.get("/orders")
async def get_orders() -> dict[str, Any]:
    """Get all orders (active, conditional, and history)"""
    try:
        active_orders = dispatcher.get_active_orders()
        conditional_orders = dispatcher.get_conditional_orders()
        order_history = dispatcher.get_order_history()
        order_summary = dispatcher.get_order_summary()

        return {
            "status": "success",
            "data": {
                "active_orders": active_orders,
                "conditional_orders": conditional_orders,
                "order_history": order_history,
                "summary": order_summary,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error(f"Error getting orders: {err}")
        raise HTTPException(status_code=500, detail="Failed to get orders")


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict[str, Any]:
    """Get specific order"""
    try:
        order = dispatcher.get_order(order_id)

        if not order:
            raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")

        return {
            "status": "success",
            "data": order,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error getting order {order_id}: {err}")
        raise HTTPException(status_code=500, detail="Failed to get order")


@app.delete("/orders/{order_id}")
async def cancel_order_by_id(order_id: str) -> dict[str, Any]:
    """Cancel an order by ID"""
    try:
        success = dispatcher.cancel_order(order_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")

        return {
            "status": "success",
            "message": f"Order {order_id} cancelled successfully",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error cancelling order {order_id}: {err}")
        raise HTTPException(status_code=500, detail="Failed to cancel order")


@app.get("/version")
async def get_version() -> dict[str, Any]:
    """Get application version information"""
    return {
        "name": "Petrosa Trading Engine",
        "version": "0.1.0",
        "description": (
            "Petrosa Trading Engine MVP - Signal-driven trading execution with "
            "multi-strategy support"
        ),
        "build_date": "2025-06-29",
        "python_version": "3.11+",
        "api_version": "v1",
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


@app.get("/openapi.json")
async def get_openapi_specs() -> dict[str, Any]:
    """Get OpenAPI specifications in JSON format"""
    return app.openapi()


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint"""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/docs")
async def get_documentation() -> dict[str, Any]:
    """Get API documentation"""
    return {
        "title": "Petrosa Trading Engine API",
        "version": "1.1.0",
        "description": (
            "Advanced cryptocurrency trading engine with multi-strategy "
            "signal aggregation"
        ),
        "endpoints": {
            "/health": "Comprehensive health check",
            "/ready": "Kubernetes readiness probe",
            "/live": "Kubernetes liveness probe",
            "/account": "Get detailed account information",
            "/trade": "Process trading signals with conflict resolution",
            "/price/{symbol}": "Get current price for a symbol",
            "/order/{symbol}/{order_id}": "Get order status",
            "/cancel_order/{symbol}/{order_id}": "Cancel an order",
            "/metrics": "Get Prometheus metrics",
        },
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
