import asyncio
import datetime
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from contracts.signal import Signal, StrategyMode
from contracts.order import TradeOrder
from shared.config import settings
from shared.logger import audit_logger
from tradeengine.dispatcher import dispatcher
from tradeengine.signal_aggregator import signal_aggregator
from tradeengine.position_manager import position_manager
from tradeengine.order_manager import order_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Petrosa Trading Engine...")

    # Initialize audit logging in background - don't block startup
    async def init_audit_logger() -> None:
        try:
            await audit_logger.initialize()
        except Exception as e:
            logger.warning("MySQL audit logger initialization failed: %s", str(e))
            logger.info("Continuing without audit logging...")

    # Start audit logger initialization in background
    asyncio.create_task(init_audit_logger())

    logger.info("Petrosa Trading Engine started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Petrosa Trading Engine...")
    await audit_logger.close()
    logger.info("Petrosa Trading Engine shut down complete")


# Initialize FastAPI app
app = FastAPI(
    title="Petrosa Trading Engine",
    description="Petrosa Trading Engine MVP - Signal-driven trading execution with multi-strategy support",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict:
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
            "Position tracking"
        ]
    }


@app.get("/health")
async def health() -> dict:
    """Detailed health check"""
    audit_status = {
        "enabled": audit_logger.enabled,
        "connected": audit_logger.connected
    }
    status = "healthy" if audit_logger.enabled and audit_logger.connected else "degraded"
    warnings = []
    if not audit_logger.enabled or not audit_logger.connected:
        warnings.append("Audit logging is not available. Real trading is disabled. Only simulation is allowed.")
    return {
        "status": status,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "environment": settings.environment,
        "components": {
            "signal_aggregator": "active",
            "position_manager": "active",
            "order_manager": "active",
            "dispatcher": "active",
            "audit_logger": audit_status
        },
        "warnings": warnings
    }


@app.get("/ready")
async def ready() -> dict:
    """Readiness probe endpoint"""
    try:
        audit_status = {
            "enabled": audit_logger.enabled,
            "connected": audit_logger.connected
        }
        if not audit_logger.enabled or not audit_logger.connected:
            raise HTTPException(status_code=503, detail="Audit logging unavailable. Not ready for real trading.")
        return {
            "status": "ready",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "audit_logger": audit_status
        }
    except Exception as err:
        logger.error("Readiness check failed: %s", str(err))
        raise HTTPException(status_code=503, detail="Service not ready") from err


@app.get("/live")
async def live() -> dict:
    """Liveness probe endpoint"""
    try:
        # Check if the application is alive and functioning
        # This should be a lightweight check
        return {
            "status": "alive",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error("Liveness check failed: %s", str(err))
        raise HTTPException(status_code=503, detail="Service not alive") from err


@app.post("/trade")
async def process_trading_signal(signal: Signal) -> dict:
    """
    Process a trading signal from any strategy
    
    This endpoint accepts signals from multiple strategies, aggregates them,
    resolves conflicts, and makes intelligent execution decisions based on
    the strategy mode (deterministic, ML light, or LLM reasoning).
    """
    try:
        logger.info(f"Received signal from {signal.strategy_id}: {signal.action} {signal.symbol} (mode: {signal.strategy_mode})")
        
        # Validate signal
        if signal.confidence < 0 or signal.confidence > 1:
            raise HTTPException(
                status_code=400, detail="Signal confidence must be between 0 and 1"
            )

        if signal.action not in ["buy", "sell", "hold", "close"]:
            raise HTTPException(
                status_code=400, detail="Signal action must be 'buy', 'sell', 'hold', or 'close'"
            )

        # Dispatch signal for execution
        result = await dispatcher.dispatch(signal)

        return {
            "status": "success",
            "message": "Signal processed successfully",
            "signal_id": signal.signal_id or signal.strategy_id,
            "strategy_id": signal.strategy_id,
            "strategy_mode": signal.strategy_mode.value,
            "result": result,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }

    except HTTPException as err:
        raise err
    except Exception as err:
        logger.error("Error processing trade signal: %s", str(err))
        raise HTTPException(status_code=500, detail="Internal server error") from err


@app.post("/order")
async def place_advanced_order(order: TradeOrder) -> dict:
    """
    Place an advanced order directly (bypassing signal processing)
    
    This endpoint allows direct order placement with full Binance parameter support.
    Use this for advanced trading strategies that need precise control over order parameters.
    """
    try:
        logger.info(f"Placing advanced order: {order.type} {order.side} {order.symbol}")
        
        # Execute the order directly
        result = await dispatcher.execute_order(order)
        
        return {
            "status": "success",
            "message": "Advanced order placed successfully",
            "order_id": result.get("order_id"),
            "result": result,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as err:
        logger.error(f"Error placing advanced order: {err}")
        raise HTTPException(status_code=500, detail=f"Order placement error: {str(err)}")


@app.get("/account")
async def get_account_info() -> dict:
    """Get detailed account information from Binance"""
    try:
        account_info = await dispatcher.get_account_info()
        
        # If there's an error, return it directly
        if "error" in account_info:
            raise HTTPException(
                status_code=500, detail=f"Failed to get account information: {account_info['error']}"
            )
        
        # If it's simulated, return the simulation message
        if account_info.get("simulated"):
            return {
                "status": "simulated",
                "message": account_info.get("message", "Account info not available in simulation mode"),
                "data": account_info
            }
        
        # Process real account data
        balances = account_info.get("balances", [])
        
        # Filter and format balances (only show non-zero balances)
        active_balances = []
        total_usdt_value = 0.0
        
        for balance in balances:
            free = float(balance.get("free", 0))
            locked = float(balance.get("locked", 0))
            total = free + locked
            
            if total > 0:  # Only include non-zero balances
                balance_info = {
                    "asset": balance.get("asset"),
                    "free": free,
                    "locked": locked,
                    "total": total,
                    "available": free > 0
                }
                active_balances.append(balance_info)
        
        # Calculate account summary
        account_summary = {
            "can_trade": account_info.get("can_trade", False),
            "can_withdraw": account_info.get("can_withdraw", False),
            "can_deposit": account_info.get("can_deposit", False),
            "total_assets": len(active_balances),
            "active_balances": len([b for b in active_balances if b["total"] > 0]),
            "commission_rates": {
                "maker": account_info.get("maker_commission", 0),
                "taker": account_info.get("taker_commission", 0),
                "buyer": account_info.get("buyer_commission", 0),
                "seller": account_info.get("seller_commission", 0)
            }
        }
        
        return {
            "status": "success",
            "message": "Account information retrieved successfully",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "summary": account_summary,
            "balances": active_balances,
            "raw_data": account_info  # Include raw data for debugging
        }
        
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error getting account info: %s", str(err))
        raise HTTPException(
            status_code=500, detail="Failed to get account information"
        ) from err


@app.get("/price/{symbol}")
async def get_symbol_price(symbol: str) -> dict:
    """Get current price for a symbol"""
    try:
        price = await dispatcher.get_symbol_price(symbol)
        return {
            "symbol": symbol,
            "price": price,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }
    except Exception as err:
        logger.error("Error getting price for %s: %s", symbol, str(err))
        raise HTTPException(
            status_code=500, detail=f"Failed to get price for {symbol}"
        ) from err


@app.delete("/order/{symbol}/{order_id}")
async def cancel_order(symbol: str, order_id: int) -> dict:
    """Cancel an existing order"""
    try:
        result = await dispatcher.cancel_order(symbol, order_id)
        return {
            "message": "Order cancelled successfully",
            "data": result,
        }
    except Exception as err:
        logger.error("Error cancelling order %s: %s", order_id, str(err))
        raise HTTPException(status_code=500, detail="Failed to cancel order") from err


@app.get("/order/{symbol}/{order_id}")
async def get_order_status(symbol: str, order_id: int) -> dict:
    """Get status of an existing order"""
    try:
        order_status = await dispatcher.get_order_status(symbol, order_id)
        return {
            "message": "Order status retrieved successfully",
            "data": order_status,
        }
    except Exception as err:
        logger.error("Error getting order status for %s: %s", order_id, str(err))
        raise HTTPException(
            status_code=500, detail="Failed to get order status"
        ) from err


@app.get("/signals/summary")
async def get_signal_summary() -> dict:
    """Get summary of signal processing and aggregation"""
    try:
        summary = signal_aggregator.get_signal_summary()
        return {
            "status": "success",
            "data": summary,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except Exception as err:
        logger.error(f"Error getting signal summary: {err}")
        raise HTTPException(status_code=500, detail="Failed to get signal summary")


@app.post("/signals/strategy/{strategy_id}/weight")
async def set_strategy_weight(strategy_id: str, weight: float) -> dict:
    """Set weight for a strategy in signal aggregation"""
    try:
        if weight < 0 or weight > 10:
            raise HTTPException(status_code=400, detail="Weight must be between 0 and 10")
        
        signal_aggregator.set_strategy_weight(strategy_id, weight)
        
        return {
            "status": "success",
            "message": f"Strategy weight set for {strategy_id}",
            "strategy_id": strategy_id,
            "weight": weight,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error setting strategy weight: {err}")
        raise HTTPException(status_code=500, detail="Failed to set strategy weight")


@app.get("/signals/active")
async def get_active_signals() -> dict:
    """Get all active signals"""
    try:
        active_signals = []
        for signal in signal_aggregator.active_signals.values():
            active_signals.append({
                "strategy_id": signal.strategy_id,
                "symbol": signal.symbol,
                "action": signal.action,
                "confidence": signal.confidence,
                "strength": signal.strength.value,
                "strategy_mode": signal.strategy_mode.value,
                "timestamp": signal.timestamp.isoformat(),
                "order_type": signal.order_type.value
            })
        
        return {
            "status": "success",
            "data": {
                "active_signals": active_signals,
                "count": len(active_signals)
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except Exception as err:
        logger.error(f"Error getting active signals: {err}")
        raise HTTPException(status_code=500, detail="Failed to get active signals")


@app.get("/positions")
async def get_positions() -> dict:
    """Get all current positions"""
    try:
        positions = position_manager.get_positions()
        portfolio_summary = position_manager.get_portfolio_summary()
        
        return {
            "status": "success",
            "data": {
                "positions": positions,
                "portfolio_summary": portfolio_summary
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except Exception as err:
        logger.error(f"Error getting positions: {err}")
        raise HTTPException(status_code=500, detail="Failed to get positions")


@app.get("/positions/{symbol}")
async def get_position(symbol: str) -> dict:
    """Get specific position"""
    try:
        position = position_manager.get_position(symbol)
        
        if not position:
            raise HTTPException(status_code=404, detail=f"Position not found for {symbol}")
        
        return {
            "status": "success",
            "data": position,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error getting position for {symbol}: {err}")
        raise HTTPException(status_code=500, detail="Failed to get position")


@app.get("/orders")
async def get_orders() -> dict:
    """Get all orders (active, conditional, and history)"""
    try:
        active_orders = order_manager.get_active_orders()
        conditional_orders = order_manager.get_conditional_orders()
        order_history = order_manager.get_order_history()
        order_summary = order_manager.get_order_summary()
        
        return {
            "status": "success",
            "data": {
                "active_orders": active_orders,
                "conditional_orders": conditional_orders,
                "order_history": order_history,
                "summary": order_summary
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except Exception as err:
        logger.error(f"Error getting orders: {err}")
        raise HTTPException(status_code=500, detail="Failed to get orders")


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    """Get specific order"""
    try:
        order = order_manager.get_order(order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
        
        return {
            "status": "success",
            "data": order,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error getting order {order_id}: {err}")
        raise HTTPException(status_code=500, detail="Failed to get order")


@app.delete("/orders/{order_id}")
async def cancel_order_by_id(order_id: str) -> dict:
    """Cancel an order by ID"""
    try:
        success = order_manager.cancel_order(order_id)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
        
        return {
            "status": "success",
            "message": f"Order {order_id} cancelled successfully",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error cancelling order {order_id}: {err}")
        raise HTTPException(status_code=500, detail="Failed to cancel order")


@app.get("/version")
async def get_version() -> dict:
    """Get application version information"""
    return {
        "name": "Petrosa Trading Engine",
        "version": "0.1.0",
        "description": "Petrosa Trading Engine MVP - Signal-driven trading execution with multi-strategy support",
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
            "Position tracking"
        ]
    }


@app.get("/openapi.json")
async def get_openapi_specs() -> dict:
    """Get OpenAPI specifications in JSON format"""
    return app.openapi()


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint"""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "tradeengine.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
