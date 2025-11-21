"""
Trading Configuration API Routes.

Provides LLM-friendly API endpoints for managing trading configurations
at global, symbol, and symbol-side levels.
"""

import logging
import os
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from contracts.trading_config import TradingConfig
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


class ValidationError(BaseModel):
    """Standardized validation error format."""

    field: str = Field(..., description="Parameter name that failed validation")
    message: str = Field(..., description="Human-readable error message")
    code: str = Field(
        ..., description="Error code (e.g., 'INVALID_TYPE', 'OUT_OF_RANGE')"
    )
    suggested_value: Optional[Any] = Field(
        None, description="Suggested correct value if applicable"
    )


class CrossServiceConflict(BaseModel):
    """Cross-service configuration conflict."""

    service: str = Field(..., description="Service name with conflicting configuration")
    conflict_type: str = Field(
        ..., description="Type of conflict (e.g., 'PARAMETER_CONFLICT')"
    )
    description: str = Field(..., description="Description of the conflict")
    resolution: str = Field(..., description="Suggested resolution")


class ValidationResponse(BaseModel):
    """Standardized validation response across all services."""

    validation_passed: bool = Field(..., description="Whether validation passed")
    errors: List[ValidationError] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[str] = Field(
        default_factory=list, description="Non-blocking warnings"
    )
    suggested_fixes: List[str] = Field(
        default_factory=list, description="Actionable suggestions to fix errors"
    )
    estimated_impact: Dict[str, Any] = Field(
        default_factory=dict,
        description="Estimated impact of configuration changes",
    )
    conflicts: List[CrossServiceConflict] = Field(
        default_factory=list, description="Cross-service conflicts detected"
    )


class ConfigValidationRequest(BaseModel):
    """Request model for configuration validation."""

    parameters: Dict[str, Any] = Field(
        ..., description="Configuration parameters to validate"
    )
    symbol: Optional[str] = Field(
        None, description="Trading symbol (optional, for symbol-specific validation)"
    )
    side: Optional[Literal["LONG", "SHORT"]] = Field(
        None,
        description="Position side (optional, for symbol-side-specific validation)",
    )


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


@router.post(
    "/validate",
    response_model=APIResponse,
    summary="Validate configuration without applying changes",
    description="""
    **For LLM Agents**: Validate configuration parameters without persisting changes.

    This endpoint performs comprehensive validation including:
    - Parameter type and constraint validation
    - Dependency validation
    - Cross-service conflict detection (future)
    - Impact assessment

    **Example Request**:
    ```json
    {
      "parameters": {
        "leverage": 15,
        "stop_loss_pct": 2.5
      },
      "symbol": "BTCUSDT",
      "side": "LONG"
    }
    ```

    **Example Response**:
    ```json
    {
      "success": true,
      "data": {
        "validation_passed": true,
        "errors": [],
        "warnings": [],
        "suggested_fixes": [],
        "estimated_impact": {
          "risk_level": "medium",
          "affected_positions": "all_long_btcusdt"
        },
        "conflicts": []
      }
    }
    ```
    """,
    tags=["trading-configuration"],
)
async def validate_config(request: ConfigValidationRequest):
    """Validate configuration without applying changes."""
    try:
        manager = get_config_manager()

        # Perform validation using existing logic
        success, config, errors = await manager.set_config(
            parameters=request.parameters,
            changed_by="validation_api",
            symbol=request.symbol.upper() if request.symbol else None,
            side=request.side,
            reason="Validation only - no changes applied",
            validate_only=True,
        )

        # Convert errors to standardized format
        validation_errors = []
        suggested_fixes = []

        for error_msg in errors:
            # Parse error message to extract field and details
            if "Unknown parameter" in error_msg:
                # Handle "Unknown parameter" errors separately (they don't contain "must be")
                code = "UNKNOWN_PARAMETER"
                # Extract parameter name from "Unknown parameter: param_name"
                if "Unknown parameter:" in error_msg:
                    param_name = error_msg.split("Unknown parameter:")[-1].strip()
                    field = param_name
                else:
                    field = "unknown"
                suggested_fixes.append(
                    f"Remove {field} or check parameter name spelling"
                )
                validation_errors.append(
                    ValidationError(
                        field=field,
                        message=error_msg,
                        code=code,
                        suggested_value=None,
                    )
                )
            elif "must be" in error_msg or "must be one of" in error_msg:
                # Extract field name (usually first word before "must")
                parts = error_msg.split(" must be")
                if parts:
                    field = parts[0].strip()
                    message = error_msg

                    # Determine error code
                    suggested_value = None  # Initialize before conditionals
                    if "must be integer" in error_msg:
                        code = "INVALID_TYPE"
                        suggested_fixes.append(f"Change {field} to an integer value")
                    elif "must be float" in error_msg:
                        code = "INVALID_TYPE"
                        suggested_fixes.append(f"Change {field} to a numeric value")
                    elif "must be >=" in error_msg or "must be <=" in error_msg:
                        code = "OUT_OF_RANGE"
                        # Extract suggested value from schema
                        from tradeengine.defaults import PARAMETER_SCHEMA

                        if field in PARAMETER_SCHEMA:
                            schema = PARAMETER_SCHEMA[field]
                            if "min" in schema and "max" in schema:
                                suggested_value = (schema["min"] + schema["max"]) / 2
                            elif "min" in schema:
                                suggested_value = schema["min"]
                            elif "max" in schema:
                                suggested_value = schema["max"]
                            else:
                                suggested_value = schema.get("default")
                        else:
                            suggested_value = None
                    elif "must be one of" in error_msg:
                        code = "INVALID_VALUE"
                        # Extract allowed values
                        allowed_start = error_msg.find("[") + 1
                        allowed_end = error_msg.find("]")
                        if allowed_start > 0 and allowed_end > allowed_start:
                            allowed_values = error_msg[allowed_start:allowed_end]
                            suggested_fixes.append(f"Use one of: {allowed_values}")
                            suggested_value = None
                        else:
                            suggested_value = None
                    else:
                        code = "VALIDATION_ERROR"
                        suggested_value = None

                    validation_errors.append(
                        ValidationError(
                            field=field,
                            message=message,
                            code=code,
                            suggested_value=suggested_value,
                        )
                    )
            else:
                # Generic error
                validation_errors.append(
                    ValidationError(
                        field="unknown",
                        message=error_msg,
                        code="VALIDATION_ERROR",
                        suggested_value=None,
                    )
                )

        # Estimate impact (simplified for now)
        estimated_impact = {
            "risk_level": "low",
            "affected_scope": (
                "global" if not request.symbol else f"symbol:{request.symbol}"
            ),
            "parameter_count": len(request.parameters),
        }

        # Add risk assessment based on parameters
        high_risk_params = ["leverage", "stop_loss_pct", "max_position_size"]
        if any(param in request.parameters for param in high_risk_params):
            estimated_impact["risk_level"] = "medium"

        if "leverage" in request.parameters:
            leverage = request.parameters["leverage"]
            # Type check to avoid comparison errors with invalid types
            if isinstance(leverage, (int, float)) and leverage > 50:
                estimated_impact["risk_level"] = "high"
                estimated_impact["warning"] = (
                    "High leverage increases risk significantly"
                )

        # Cross-service conflict detection
        conflicts = await detect_cross_service_conflicts(
            request.parameters, request.symbol, request.side
        )

        validation_response = ValidationResponse(
            validation_passed=success and len(validation_errors) == 0,
            errors=validation_errors,
            warnings=[],
            suggested_fixes=suggested_fixes,
            estimated_impact=estimated_impact,
            conflicts=conflicts,
        )

        return APIResponse(
            success=True,
            data=validation_response,
            metadata={
                "validation_mode": "dry_run",
                "scope": (
                    "global"
                    if not request.symbol
                    else f"{request.symbol}:{request.side or 'all'}"
                ),
            },
        )

    except Exception as e:
        logger.error(f"Error validating config: {e}")
        return APIResponse(
            success=False,
            error={"code": "INTERNAL_ERROR", "message": str(e)},
        )


# Service URLs for cross-service conflict detection
SERVICE_URLS = {
    "data-manager": os.getenv("DATA_MANAGER_URL", "http://petrosa-data-manager:8080"),
    "ta-bot": os.getenv("TA_BOT_URL", "http://petrosa-ta-bot:8080"),
    "realtime-strategies": os.getenv(
        "REALTIME_STRATEGIES_URL", "http://petrosa-realtime-strategies:8080"
    ),
}

# Constants for conflict detection
CONFLICT_TIMEOUT_SECONDS = 5.0  # Timeout for cross-service conflict checks
POSITION_MISMATCH_THRESHOLD = 0.2  # 20% threshold for position limit mismatches
MAX_ERROR_MESSAGES_TO_SHOW = 2  # Limit error messages shown in conflicts


async def detect_cross_service_conflicts(
    parameters: Dict[str, Any],
    symbol: Optional[str] = None,
    side: Optional[Literal["LONG", "SHORT"]] = None,
) -> List[CrossServiceConflict]:
    """
    Detect cross-service configuration conflicts.

    Queries other services' /api/v1/config/validate endpoints to check for
    conflicting configurations.

    Args:
        parameters: Configuration parameters to check
        symbol: Trading symbol (optional)
        side: Position side (optional)

    Returns:
        List of CrossServiceConflict objects
    """
    conflicts = []
    timeout = httpx.Timeout(CONFLICT_TIMEOUT_SECONDS)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Check data-manager for conflicts (if configuring confidence thresholds or position limits)
        if any(
            param in parameters
            for param in [
                "leverage",
                "max_position_size",
                "stop_loss_pct",
                "take_profit_pct",
            ]
        ):
            # Check if data-manager has conflicting confidence or position settings
            if "max_position_size" in parameters:
                # Query data-manager's current config to check for conflicts
                try:
                    response = await client.get(
                        f"{SERVICE_URLS['data-manager']}/api/v1/config/application",
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("success") and data.get("data"):
                            current_max = data["data"].get("max_positions")
                            proposed_max = parameters.get("max_position_size")
                            if current_max and proposed_max:
                                # Type check and convert to float for comparison
                                try:
                                    current_max_float = float(current_max)
                                    proposed_max_float = float(proposed_max)
                                except (ValueError, TypeError):
                                    logger.debug(
                                        "Invalid type for position limit comparison"
                                    )
                                else:
                                    # Check if there's a significant mismatch
                                    if (
                                        abs(current_max_float - proposed_max_float)
                                        > current_max_float
                                        * POSITION_MISMATCH_THRESHOLD
                                    ):
                                        conflicts.append(
                                            CrossServiceConflict(
                                                service="data-manager",
                                                conflict_type="PARAMETER_CONFLICT",
                                                description=(
                                                    f"Position limit mismatch: tradeengine proposes "
                                                    f"max_position_size={proposed_max}, but data-manager has "
                                                    f"max_positions={current_max}"
                                                ),
                                                resolution=(
                                                    "Align max_position_size in tradeengine with "
                                                    "max_positions in data-manager"
                                                ),
                                            )
                                        )
                except Exception as e:
                    logger.debug(f"Could not check data-manager for conflicts: {e}")

        # Check ta-bot and realtime-strategies for strategy config conflicts
        # These services might have conflicting confidence thresholds or strategy parameters
        if any(
            param in parameters
            for param in ["leverage", "stop_loss_pct", "take_profit_pct"]
        ):
            for service_name, service_url in [
                ("ta-bot", SERVICE_URLS["ta-bot"]),
                ("realtime-strategies", SERVICE_URLS["realtime-strategies"]),
            ]:
                try:
                    # Query the service's validate endpoint with relevant parameters
                    # Note: These services use strategy_id, so we'll check for general conflicts
                    validation_request = {
                        "parameters": {
                            k: v
                            for k, v in parameters.items()
                            if k in ["leverage", "stop_loss_pct", "take_profit_pct"]
                        },
                    }
                    if symbol:
                        validation_request["symbol"] = symbol

                    response = await client.post(
                        f"{service_url}/api/v1/config/validate",
                        json=validation_request,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("success") and data.get("data"):
                            validation_data = data["data"]
                            # Check if the service reports conflicts or validation issues
                            if not validation_data.get("validation_passed", True):
                                errors = validation_data.get("errors", [])
                                if errors:
                                    conflicts.append(
                                        CrossServiceConflict(
                                            service=service_name,
                                            conflict_type="VALIDATION_CONFLICT",
                                            description=(
                                                f"{service_name} reports validation errors for "
                                                f"trading parameters: "
                                                f"{', '.join([e.get('message', '') for e in errors[:MAX_ERROR_MESSAGES_TO_SHOW]])}"
                                            ),
                                            resolution=(
                                                f"Review {service_name} validation errors and "
                                                "ensure parameter compatibility"
                                            ),
                                        )
                                    )

                except httpx.TimeoutException:
                    logger.debug(f"Timeout checking {service_name} for conflicts")
                except Exception as e:
                    logger.debug(f"Error checking {service_name} conflicts: {e}")

    return conflicts


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


# =============================================================================
# Position Limit Management Endpoints
# =============================================================================


@router.put("/config/limits/global", response_model=APIResponse)
async def set_global_limits(
    max_position_size: Optional[float] = None,
    max_accumulations: Optional[int] = None,
    accumulation_cooldown_seconds: Optional[int] = None,
) -> APIResponse:
    """
    Set global position limits (applies to all symbols unless overridden).

    Args:
        max_position_size: Maximum quantity per position (e.g., 100.0)
        max_accumulations: Maximum accumulation count (e.g., 3)
        accumulation_cooldown_seconds: Cooldown between accumulations (e.g., 300)
    """
    try:
        manager = get_config_manager()

        # Get existing global config or create new
        config = await manager.get_config() or TradingConfig(
            id="global", parameters={}, created_by="api"
        )

        # Update limits
        if max_position_size is not None:
            config.parameters["max_position_size"] = max_position_size
        if max_accumulations is not None:
            config.parameters["max_accumulations"] = max_accumulations
        if accumulation_cooldown_seconds is not None:
            config.parameters["accumulation_cooldown_seconds"] = (
                accumulation_cooldown_seconds
            )

        # Save config
        success = await manager.set_config(config)

        if success:
            return APIResponse(
                success=True,
                data={
                    "config": {
                        "id": config.id,
                        "parameters": config.parameters,
                        "created_at": config.created_at.isoformat(),
                        "updated_at": config.updated_at.isoformat(),
                    }
                },
                message="Global limits updated successfully",
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to update global limits"
            )

    except Exception as e:
        logger.error(f"Error setting global limits: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.put("/config/limits/symbol/{symbol}", response_model=APIResponse)
async def set_symbol_limits(
    symbol: str,
    max_position_size: Optional[float] = None,
    max_accumulations: Optional[int] = None,
    accumulation_cooldown_seconds: Optional[int] = None,
) -> APIResponse:
    """
    Set position limits for specific symbol (overrides global limits).

    Args:
        symbol: Trading symbol (e.g., BTCUSDT)
        max_position_size: Maximum quantity per position
        max_accumulations: Maximum accumulation count
        accumulation_cooldown_seconds: Cooldown between accumulations
    """
    try:
        manager = get_config_manager()

        # Get existing symbol config or create new
        config = await manager.get_config(symbol=symbol) or TradingConfig(
            id=f"symbol_{symbol}", symbol=symbol, parameters={}, created_by="api"
        )

        # Update limits
        if max_position_size is not None:
            config.parameters["max_position_size"] = max_position_size
        if max_accumulations is not None:
            config.parameters["max_accumulations"] = max_accumulations
        if accumulation_cooldown_seconds is not None:
            config.parameters["accumulation_cooldown_seconds"] = (
                accumulation_cooldown_seconds
            )

        # Save config
        success = await manager.set_config(config, symbol=symbol)

        if success:
            return APIResponse(
                success=True,
                data={
                    "config": {
                        "id": config.id,
                        "symbol": config.symbol,
                        "parameters": config.parameters,
                        "created_at": config.created_at.isoformat(),
                        "updated_at": config.updated_at.isoformat(),
                    }
                },
                message=f"Symbol limits updated for {symbol}",
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"Failed to update limits for {symbol}"
            )

    except Exception as e:
        logger.error(f"Error setting symbol limits: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.get("/config/limits", response_model=APIResponse)
async def get_all_limits() -> APIResponse:
    """Get all position limits (global and symbol-specific)."""
    try:
        manager = get_config_manager()
        limits = {"global": None, "symbols": {}}

        # Get global config
        global_config = await manager.get_config()
        if global_config:
            limits["global"] = {
                "max_position_size": global_config.parameters.get("max_position_size"),
                "max_accumulations": global_config.parameters.get("max_accumulations"),
                "accumulation_cooldown_seconds": global_config.parameters.get(
                    "accumulation_cooldown_seconds"
                ),
            }

        # Get all symbol configs
        # Note: This requires listing all symbols or iterating through configs
        from shared.constants import SUPPORTED_SYMBOLS

        for symbol in SUPPORTED_SYMBOLS:
            symbol_config = await manager.get_config(symbol=symbol)
            if symbol_config and (
                symbol_config.parameters.get("max_position_size")
                or symbol_config.parameters.get("max_accumulations")
                or symbol_config.parameters.get("accumulation_cooldown_seconds")
            ):
                limits["symbols"][symbol] = {
                    "max_position_size": symbol_config.parameters.get(
                        "max_position_size"
                    ),
                    "max_accumulations": symbol_config.parameters.get(
                        "max_accumulations"
                    ),
                    "accumulation_cooldown_seconds": symbol_config.parameters.get(
                        "accumulation_cooldown_seconds"
                    ),
                }

        return APIResponse(
            success=True,
            data={"limits": limits},
            message="Position limits retrieved successfully",
        )

    except Exception as e:
        logger.error(f"Error getting limits: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )


@router.delete("/config/limits/symbol/{symbol}", response_model=APIResponse)
async def delete_symbol_limits(symbol: str) -> APIResponse:
    """Delete symbol-specific limits (revert to global limits)."""
    try:
        manager = get_config_manager()
        success = await manager.delete_config(symbol=symbol)

        if success:
            return APIResponse(
                success=True,
                message=f"Symbol limits deleted for {symbol}, using global limits",
            )
        else:
            raise HTTPException(status_code=404, detail=f"No limits found for {symbol}")

    except Exception as e:
        logger.error(f"Error deleting symbol limits: {e}")
        return APIResponse(
            success=False, error={"code": "INTERNAL_ERROR", "message": str(e)}
        )
