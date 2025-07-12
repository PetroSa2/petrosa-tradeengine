import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from contracts.signal import Signal
from shared.config import settings
from shared.logger import audit_logger
from tradeengine.dispatcher import dispatcher

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
    description="Petrosa Trading Engine MVP - Signal-driven trading execution",
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
    }


@app.get("/health")
async def health() -> dict:
    """Detailed health check"""
    return {
        "status": "healthy",
        "timestamp": "2025-06-29T00:00:00Z",
        "environment": settings.environment,
    }


@app.get("/ready")
async def ready() -> dict:
    """Readiness probe endpoint"""
    try:
        # Check if the application is ready to receive traffic
        # Add any necessary checks here (database, external services, etc.)
        return {
            "status": "ready",
            "timestamp": "2025-06-29T00:00:00Z",
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
            "timestamp": "2025-06-29T00:00:00Z",
        }
    except Exception as err:
        logger.error("Liveness check failed: %s", str(err))
        raise HTTPException(status_code=503, detail="Service not alive") from err


@app.post("/trade")
async def create_trade(signal: Signal) -> dict:
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

    except HTTPException as err:
        raise err
    except Exception as err:
        logger.error("Error processing trade signal: %s", str(err))
        raise HTTPException(status_code=500, detail="Internal server error") from err


@app.get("/account")
async def get_account_info() -> dict:
    """Get account information from Binance"""
    try:
        account_info = await dispatcher.get_account_info()
        return {
            "message": "Account information retrieved successfully",
            "data": account_info,
        }
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
            "timestamp": "2025-06-29T00:00:00Z",
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


@app.get("/version")
async def get_version() -> dict:
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
