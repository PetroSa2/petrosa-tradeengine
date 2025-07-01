from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from contracts.signal import Signal
from tradeengine.dispatcher import dispatcher
from shared.logger import audit_logger
from shared.config import settings
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import logging

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Petrosa Trading Engine",
    description="Petrosa Trading Engine MVP - Signal-driven trading execution",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event():
    """Initialize application components"""
    logger.info("Starting Petrosa Trading Engine...")
    # MongoDB initialization temporarily disabled for demo
    # try:
    #     await audit_logger.initialize()
    # except Exception as e:
    #     logger.warning("MongoDB audit logger initialization failed: %s", str(e))
    #     logger.info("Continuing without audit logging...")
    logger.info("Petrosa Trading Engine started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup application components"""
    logger.info("Shutting down Petrosa Trading Engine...")
    await audit_logger.close()
    logger.info("Petrosa Trading Engine shut down complete")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Petrosa Trading Engine",
        "version": "0.1.0",
        "status": "running",
        "environment": settings.environment,
    }


@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "timestamp": "2025-06-29T00:00:00Z",
        "environment": settings.environment,
    }


@app.get("/ready")
async def ready():
    """Readiness probe endpoint"""
    try:
        # Check if the application is ready to receive traffic
        # Add any necessary checks here (database, external services, etc.)
        return {
            "status": "ready",
            "timestamp": "2025-06-29T00:00:00Z",
        }
    except Exception as e:
        logger.error("Readiness check failed: %s", str(e))
        raise HTTPException(status_code=503, detail="Service not ready")


@app.get("/live")
async def live():
    """Liveness probe endpoint"""
    try:
        # Check if the application is alive and functioning
        # This should be a lightweight check
        return {
            "status": "alive",
            "timestamp": "2025-06-29T00:00:00Z",
        }
    except Exception as e:
        logger.error("Liveness check failed: %s", str(e))
        raise HTTPException(status_code=503, detail="Service not alive")


@app.post("/trade")
async def create_trade(signal: Signal):
    """
    Process a trading signal and execute trade

    Accepts a Signal schema (JSON) and dispatches it for execution
    """
    try:
        logger.info("Received trade signal for symbol: %s", signal.symbol)

        # Validate signal
        if signal.confidence < 0 or signal.confidence > 1:
            raise HTTPException(
                status_code=400, detail="Signal confidence must be between 0 and 1"
            )

        if signal.action not in ["buy", "sell", "hold"]:
            raise HTTPException(
                status_code=400, detail="Signal action must be 'buy', 'sell', or 'hold'"
            )

        # Dispatch signal for execution
        result = await dispatcher.dispatch(signal)

        return {
            "message": "Signal processed successfully",
            "signal_id": signal.strategy_id,
            "result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error processing trade signal: %s", str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/account")
async def get_account_info():
    """Get account information from Binance"""
    try:
        account_info = await dispatcher.get_account_info()
        return {
            "message": "Account information retrieved successfully",
            "data": account_info,
        }
    except Exception as e:
        logger.error("Error getting account info: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to get account information")


@app.get("/price/{symbol}")
async def get_symbol_price(symbol: str):
    """Get current price for a symbol"""
    try:
        price = await dispatcher.get_symbol_price(symbol)
        return {
            "symbol": symbol,
            "price": price,
            "timestamp": "2025-06-29T00:00:00Z",
        }
    except Exception as e:
        logger.error("Error getting price for %s: %s", symbol, str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get price for {symbol}")


@app.delete("/order/{symbol}/{order_id}")
async def cancel_order(symbol: str, order_id: int):
    """Cancel an existing order"""
    try:
        result = await dispatcher.cancel_order(symbol, order_id)
        return {
            "message": "Order cancelled successfully",
            "data": result,
        }
    except Exception as e:
        logger.error("Error cancelling order %s: %s", order_id, str(e))
        raise HTTPException(status_code=500, detail="Failed to cancel order")


@app.get("/order/{symbol}/{order_id}")
async def get_order_status(symbol: str, order_id: int):
    """Get status of an existing order"""
    try:
        order_status = await dispatcher.get_order_status(symbol, order_id)
        return {
            "message": "Order status retrieved successfully",
            "data": order_status,
        }
    except Exception as e:
        logger.error("Error getting order status for %s: %s", order_id, str(e))
        raise HTTPException(status_code=500, detail="Failed to get order status")


@app.get("/version")
async def get_version():
    """Get application version information"""
    return {
        "name": "Petrosa Trading Engine",
        "version": "0.1.0",
        "description": "Petrosa Trading Engine MVP - Signal-driven trading execution",
        "build_date": "2025-06-29",
        "python_version": "3.10+",
        "api_version": "v1",
    }


@app.get("/openapi.json")
async def get_openapi_specs():
    """Get OpenAPI specifications in JSON format"""
    return app.openapi()


@app.get("/metrics")
async def metrics():
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
