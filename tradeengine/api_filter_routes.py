"""
Trading Filter API Routes.

Provides API endpoints for managing trading filters at strategy level.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from contracts.trading_config import TradingConfig
from tradeengine.config_manager import TradingConfigManager

logger = logging.getLogger(__name__)

# Global config manager instance (will be injected)
_config_manager: TradingConfigManager | None = None


def set_config_manager(manager: TradingConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def get_config_manager() -> TradingConfigManager:
    """Get the global config manager instance."""
    if _config_manager is None:
        raise HTTPException(
            status_code=500, detail="Configuration manager not initialized"
        )
    return _config_manager


# =============================================================================
# Request/Response Models
# =============================================================================


class FilterUpdateRequest(BaseModel):
    """Request model for updating filters."""

    filters: dict[str, Any] = Field(..., description="Filter parameters to update")
    changed_by: str = Field(
        ..., description="Who is making this change (e.g., 'llm_agent_v1', 'admin')"
    )
    reason: str | None = Field(None, description="Reason for the filter change")


class APIResponse(BaseModel):
    """Standard API response wrapper."""

    success: bool = Field(..., description="Whether operation succeeded")
    data: Any | None = Field(None, description="Response data")
    error: dict[str, Any | None] = Field(None, description="Error details if failed")
    metadata: dict[str, Any | None] = Field(None, description="Additional metadata")


# =============================================================================
# API Router
# =============================================================================

router = APIRouter(prefix="/api/v1/config/filters", tags=["trading-filters"])


@router.get("/strategy/{strategy_id}", response_model=APIResponse)
async def get_strategy_filters(
    strategy_id: str = Path(..., description="Strategy ID"),
) -> APIResponse:
    """Get strategy-specific filters."""
    try:
        manager = get_config_manager()
        # Get config with strategy_id (this will be implemented in config_manager)
        config = await manager.get_config(
            symbol=None, side=None, strategy_id=strategy_id
        )

        # Extract only filter-related parameters
        filters = {
            key: value
            for key, value in config.items()
            if key
            in [
                "tp_distance_min_pct",
                "tp_distance_max_pct",
                "sl_distance_min_pct",
                "sl_distance_max_pct",
                "price_min_absolute",
                "price_max_absolute",
                "price_min_relative_pct",
                "price_max_relative_pct",
                "quantity_min",
                "quantity_max",
                "enabled_sides",
            ]
        }

        return APIResponse(
            success=True,
            data={"strategy_id": strategy_id, "filters": filters},
            metadata={
                "message": f"Filters for strategy {strategy_id} retrieved successfully"
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting strategy filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.put("/strategy/{strategy_id}", response_model=APIResponse)
async def update_strategy_filters(
    request: FilterUpdateRequest,
    strategy_id: str = Path(..., description="Strategy ID"),
) -> APIResponse:
    """Update strategy-specific filters."""
    try:
        manager = get_config_manager()

        config = TradingConfig(
            strategy_id=strategy_id,
            symbol=None,
            side=None,
            parameters=request.filters,
            created_by=request.changed_by,
            metadata={"reason": request.reason} if request.reason else {},
        )

        # Save via mongodb client
        if manager.mongodb_client:
            success = await manager.mongodb_client.upsert_strategy_config(config)

            if success:
                manager.invalidate_cache(strategy_id=strategy_id)

                return APIResponse(
                    success=True,
                    data={"config": config.model_dump()},
                    metadata={
                        "message": (
                            f"Filters for strategy {strategy_id} updated successfully"
                        )
                    },
                )
            else:
                return APIResponse(
                    success=False,
                    error={
                        "code": "UPDATE_FAILED",
                        "message": "Failed to update strategy filters",
                    },
                )
        else:
            return APIResponse(
                success=False,
                error={"code": "NO_DB", "message": "MongoDB client not configured"},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating strategy filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


# =============================================================================
# Global, Pair, and Side Routes
# =============================================================================


@router.get("/global", response_model=APIResponse)
async def get_global_filters() -> APIResponse:
    """Get global filters."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=None, side=None)

        return APIResponse(
            success=True,
            data={"filters": config},
            message="Global filters retrieved successfully",
        )
    except Exception as e:
        logger.error(f"Error getting global filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.put("/global", response_model=APIResponse)
async def update_global_filters(request: FilterUpdateRequest) -> APIResponse:
    """Update global filters."""
    try:
        manager = get_config_manager()
        success, config, errors = await manager.set_config(
            parameters=request.filters,
            changed_by=request.changed_by,
            reason=request.reason,
            symbol=None,
            side=None,
        )

        if success:
            return APIResponse(
                success=True,
                data={"config": config.model_dump() if config else None},
                message="Global filters updated successfully",
            )
        else:
            return APIResponse(
                success=False,
                error={"code": "UPDATE_FAILED", "message": ", ".join(errors)},
            )
    except Exception as e:
        logger.error(f"Error updating global filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get("/pair/{symbol}", response_model=APIResponse)
async def get_pair_filters(
    symbol: str = Path(..., description="Trading symbol (e.g., BTCUSDT)"),
) -> APIResponse:
    """Get pair-specific filters."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=symbol, side=None)

        return APIResponse(
            success=True,
            data={"symbol": symbol, "filters": config},
            message=f"Filters for {symbol} retrieved successfully",
        )
    except Exception as e:
        logger.error(f"Error getting pair filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.put("/pair/{symbol}", response_model=APIResponse)
async def update_pair_filters(
    request: FilterUpdateRequest,
    symbol: str = Path(..., description="Trading symbol (e.g., BTCUSDT)"),
) -> APIResponse:
    """Update pair-specific filters."""
    try:
        manager = get_config_manager()
        success, config, errors = await manager.set_config(
            parameters=request.filters,
            changed_by=request.changed_by,
            reason=request.reason,
            symbol=symbol,
            side=None,
        )

        if success:
            return APIResponse(
                success=True,
                data={"config": config.model_dump() if config else None},
                message=f"Filters for {symbol} updated successfully",
            )
        else:
            return APIResponse(
                success=False,
                error={"code": "UPDATE_FAILED", "message": ", ".join(errors)},
            )
    except Exception as e:
        logger.error(f"Error updating pair filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get("/pair/{symbol}/side/{side}", response_model=APIResponse)
async def get_side_filters(
    symbol: str = Path(..., description="Trading symbol (e.g., BTCUSDT)"),
    side: Literal["LONG", "SHORT"] = Path(..., description="Position side"),
) -> APIResponse:
    """Get symbol-side specific filters."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=symbol, side=side)

        return APIResponse(
            success=True,
            data={"symbol": symbol, "side": side, "filters": config},
            message=f"Filters for {symbol}-{side} retrieved successfully",
        )
    except Exception as e:
        logger.error(f"Error getting side filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.put("/pair/{symbol}/side/{side}", response_model=APIResponse)
async def update_side_filters(
    request: FilterUpdateRequest,
    symbol: str = Path(..., description="Trading symbol (e.g., BTCUSDT)"),
    side: Literal["LONG", "SHORT"] = Path(..., description="Position side"),
) -> APIResponse:
    """Update symbol-side specific filters."""
    try:
        manager = get_config_manager()
        success, config, errors = await manager.set_config(
            parameters=request.filters,
            changed_by=request.changed_by,
            reason=request.reason,
            symbol=symbol,
            side=side,
        )

        if success:
            return APIResponse(
                success=True,
                data={"config": config.model_dump() if config else None},
                message=f"Filters for {symbol}-{side} updated successfully",
            )
        else:
            return APIResponse(
                success=False,
                error={"code": "UPDATE_FAILED", "message": ", ".join(errors)},
            )
    except Exception as e:
        logger.error(f"Error updating side filters: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )
