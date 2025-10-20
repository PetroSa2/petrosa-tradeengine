"""
Trading Configuration API Routes.

Provides LLM-friendly API endpoints for managing trading configurations
at global, symbol, and symbol-side levels.
"""

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from tradeengine.config_manager import TradingConfigManager
from tradeengine.defaults import get_default_parameters, get_parameter_schema

logger = logging.getLogger(__name__)

# Global config manager instance (will be injected)
_config_manager: Optional[TradingConfigManager] = None


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


class ConfigUpdateRequest(BaseModel):
    """Request model for updating configuration."""

    parameters: Dict[str, Any] = Field(
        ..., description="Configuration parameters to update"
    )
    changed_by: str = Field(
        ..., description="Who is making this change (e.g., 'llm_agent_v1', 'admin')"
    )
    reason: Optional[str] = Field(
        None, description="Reason for the configuration change"
    )
    validate_only: bool = Field(
        False, description="If true, only validate parameters without saving"
    )


class ConfigResponse(BaseModel):
    """Response model for configuration queries."""

    symbol: Optional[str] = Field(None, description="Trading symbol")
    side: Optional[Literal["LONG", "SHORT"]] = Field(None, description="Position side")
    parameters: Dict[str, Any] = Field(..., description="Configuration parameters")
    version: int = Field(..., description="Configuration version")
    source: str = Field(..., description="Configuration source (mongodb/mysql/default)")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")


class APIResponse(BaseModel):
    """Standard API response wrapper."""

    success: bool = Field(..., description="Whether operation succeeded")
    data: Optional[Any] = Field(None, description="Response data")
    error: Optional[Dict[str, Any]] = Field(None, description="Error details if failed")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


# =============================================================================
# API Router
# =============================================================================

router = APIRouter(prefix="/api/v1/config", tags=["trading-configuration"])


@router.get(
    "/trading/schema",
    response_model=APIResponse,
    summary="Get parameter schema",
    description="""
    **For LLM Agents**: Get complete parameter schema with validation rules.

    Returns all available trading parameters with:
    - Parameter names and types
    - Descriptions (when to use, how it affects trading)
    - Default values
    - Validation rules (min, max, allowed values)
    - Examples and impact descriptions

    Use this endpoint first to discover what parameters you can configure
    and understand their effects before making changes.

    **Example Response**:
    ```json
    {
      "success": true,
      "data": {
        "leverage": {
          "type": "integer",
          "description": "Leverage multiplier for futures trading...",
          "default": 10,
          "min": 1,
          "max": 125,
          "example": 10
        },
        ...
      }
    }
    ```
    """,
)
async def get_schema():
    """Get parameter schema."""
    try:
        schema = get_parameter_schema()
        return APIResponse(
            success=True, data=schema, metadata={"total_parameters": len(schema)}
        )
    except Exception as e:
        logger.error(f"Error getting schema: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get(
    "/trading/defaults",
    response_model=APIResponse,
    summary="Get default parameters",
    description="""
    **For LLM Agents**: Get all default parameter values.

    Returns the hardcoded default values used when no custom configuration exists.
    Useful for understanding baseline configuration and resetting to defaults.
    """,
)
async def get_defaults():
    """Get default parameters."""
    try:
        defaults = get_default_parameters()
        return APIResponse(
            success=True, data=defaults, metadata={"total_parameters": len(defaults)}
        )
    except Exception as e:
        logger.error(f"Error getting defaults: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get(
    "/trading",
    response_model=APIResponse,
    summary="Get global configuration",
    description="""
    **For LLM Agents**: Get global trading configuration.

    Global configuration applies to all trading symbols and sides unless
    overridden by symbol-specific or symbol-side-specific configs.

    Returns the current global settings and indicates whether they come from
    database or defaults.
    """,
)
async def get_global_config():
    """Get global configuration."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=None, side=None)

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=None,
                side=None,
                parameters=config,
                version=1,
                source="resolved",
                created_at=None,
                updated_at=None,
            ),
            metadata={"scope": "global"},
        )
    except Exception as e:
        logger.error(f"Error getting global config: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.post(
    "/trading",
    response_model=APIResponse,
    summary="Update global configuration",
    description="""
    **For LLM Agents**: Update global trading configuration.

    **Steps**:
    1. Call GET /trading/schema to see available parameters
    2. Prepare your parameter updates
    3. POST to this endpoint with parameters and changed_by
    4. Configuration takes effect within 60 seconds (cache TTL)

    Global configuration affects all symbols and sides. Use this for
    system-wide parameter changes.

    **Example Request**:
    ```json
    {
      "parameters": {
        "leverage": 15,
        "stop_loss_pct": 2.5,
        "risk_management_enabled": true
      },
      "changed_by": "llm_agent_v1",
      "reason": "Adjusting risk parameters for market conditions"
    }
    ```
    """,
)
async def update_global_config(request: ConfigUpdateRequest):
    """Update global configuration."""
    try:
        manager = get_config_manager()

        success, config, errors = await manager.set_config(
            parameters=request.parameters,
            changed_by=request.changed_by,
            symbol=None,
            side=None,
            reason=request.reason,
            validate_only=request.validate_only,
        )

        if not success:
            return APIResponse(
                success=False,
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Parameter validation failed",
                    "details": {"errors": errors},
                },
            )

        if request.validate_only:
            return APIResponse(
                success=True,
                data=None,
                metadata={
                    "validation": "passed",
                    "message": "Parameters valid but not saved (validate_only=true)",
                },
            )

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=None,
                side=None,
                parameters=config.parameters if config else {},
                version=config.version if config else 1,
                source="mongodb",
                created_at=config.created_at.isoformat() if config else None,
                updated_at=config.updated_at.isoformat() if config else None,
            ),
            metadata={"action": "updated", "scope": "global"},
        )

    except Exception as e:
        logger.error(f"Error updating global config: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get(
    "/trading/{symbol}",
    response_model=APIResponse,
    summary="Get symbol configuration",
    description="""
    **For LLM Agents**: Get configuration for specific trading symbol.

    Returns resolved configuration for the symbol, which includes global config
    merged with symbol-specific overrides.

    **Example**: GET /trading/BTCUSDT returns config for Bitcoin trading.
    """,
)
async def get_symbol_config(
    symbol: str = Path(..., description="Trading symbol (e.g., BTCUSDT)")
):
    """Get symbol configuration."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=symbol.upper(), side=None)

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=symbol.upper(),
                side=None,
                parameters=config,
                version=1,
                source="resolved",
                created_at=None,
                updated_at=None,
            ),
            metadata={"scope": "symbol"},
        )
    except Exception as e:
        logger.error(f"Error getting symbol config for {symbol}: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.post(
    "/trading/{symbol}",
    response_model=APIResponse,
    summary="Update symbol configuration",
    description="""
    **For LLM Agents**: Update configuration for specific trading symbol.

    Creates symbol-specific overrides that apply to both LONG and SHORT
    positions for this symbol. These override global settings.

    **Example**: Configure BTCUSDT with higher leverage than global default.
    """,
)
async def update_symbol_config(
    request: ConfigUpdateRequest, symbol: str = Path(..., description="Trading symbol")
):
    """Update symbol configuration."""
    try:
        manager = get_config_manager()

        success, config, errors = await manager.set_config(
            parameters=request.parameters,
            changed_by=request.changed_by,
            symbol=symbol.upper(),
            side=None,
            reason=request.reason,
            validate_only=request.validate_only,
        )

        if not success:
            return APIResponse(
                success=False,
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Parameter validation failed",
                    "details": {"errors": errors},
                },
            )

        if request.validate_only:
            return APIResponse(
                success=True, data=None, metadata={"validation": "passed"}
            )

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=symbol.upper(),
                side=None,
                parameters=config.parameters if config else {},
                version=config.version if config else 1,
                source="mongodb",
                created_at=config.created_at.isoformat() if config else None,
                updated_at=config.updated_at.isoformat() if config else None,
            ),
            metadata={"action": "updated", "scope": "symbol"},
        )

    except Exception as e:
        logger.error(f"Error updating symbol config for {symbol}: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get(
    "/trading/{symbol}/{side}",
    response_model=APIResponse,
    summary="Get symbol-side configuration",
    description="""
    **For LLM Agents**: Get configuration for specific symbol and position side.

    Returns fully resolved configuration that includes:
    - Global config
    - Symbol-specific overrides
    - Symbol-side-specific overrides (highest priority)

    **Example**: GET /trading/BTCUSDT/LONG returns config for BTC long positions.
    Side must be either "LONG" or "SHORT".
    """,
)
async def get_symbol_side_config(
    symbol: str = Path(..., description="Trading symbol"),
    side: Literal["LONG", "SHORT"] = Path(..., description="Position side"),
):
    """Get symbol-side configuration."""
    try:
        manager = get_config_manager()
        config = await manager.get_config(symbol=symbol.upper(), side=side)

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=symbol.upper(),
                side=side,
                parameters=config,
                version=1,
                source="resolved",
                created_at=None,
                updated_at=None,
            ),
            metadata={"scope": "symbol_side"},
        )
    except Exception as e:
        logger.error(f"Error getting config for {symbol}-{side}: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.post(
    "/trading/{symbol}/{side}",
    response_model=APIResponse,
    summary="Update symbol-side configuration",
    description="""
    **For LLM Agents**: Update configuration for specific symbol and position side.

    Creates the most specific configuration override. These settings only affect
    the specified symbol and side (LONG or SHORT), overriding both global and
    symbol-level configurations.

    **Use Cases**:
    - Set different leverage for LONG vs SHORT on same symbol
    - Configure tighter stop loss for SHORT positions
    - Enable/disable specific direction for a symbol

    **Example**: Configure BTCUSDT LONG positions with 20x leverage while
    keeping SHORT positions at 10x.
    """,
)
async def update_symbol_side_config(
    request: ConfigUpdateRequest,
    symbol: str = Path(..., description="Trading symbol"),
    side: Literal["LONG", "SHORT"] = Path(..., description="Position side"),
):
    """Update symbol-side configuration."""
    try:
        manager = get_config_manager()

        success, config, errors = await manager.set_config(
            parameters=request.parameters,
            changed_by=request.changed_by,
            symbol=symbol.upper(),
            side=side,
            reason=request.reason,
            validate_only=request.validate_only,
        )

        if not success:
            return APIResponse(
                success=False,
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Parameter validation failed",
                    "details": {"errors": errors},
                },
            )

        if request.validate_only:
            return APIResponse(
                success=True, data=None, metadata={"validation": "passed"}
            )

        return APIResponse(
            success=True,
            data=ConfigResponse(
                symbol=symbol.upper(),
                side=side,
                parameters=config.parameters if config else {},
                version=config.version if config else 1,
                source="mongodb",
                created_at=config.created_at.isoformat() if config else None,
                updated_at=config.updated_at.isoformat() if config else None,
            ),
            metadata={"action": "updated", "scope": "symbol_side"},
        )

    except Exception as e:
        logger.error(f"Error updating config for {symbol}-{side}: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get(
    "/health",
    summary="Configuration system health check",
    description="Check if configuration system is operational",
)
async def config_health_check():
    """Health check for configuration system."""
    try:
        manager = get_config_manager()
        mongodb_connected = (
            manager.mongodb_client.connected if manager.mongodb_client else False
        )

        return {
            "status": "healthy" if mongodb_connected else "degraded",
            "mongodb_connected": mongodb_connected,
            "cache_size": len(manager._cache),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
