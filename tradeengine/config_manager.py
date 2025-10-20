"""
Trading Configuration Manager with Caching and Fallback Logic.

Manages runtime trading configuration with:
- MongoDB primary persistence
- MySQL fallback (future)
- TTL-based caching
- Configuration hierarchy resolution
- Automatic default persistence
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.db.mongodb_client import MongoDBClient
from tradeengine.db.mysql_config_repository import MySQLConfigRepository
from tradeengine.defaults import (
    get_default_parameters,
    merge_parameters,
    validate_parameters,
)

logger = logging.getLogger(__name__)


class TradingConfigManager:
    """
    Trading configuration manager with caching and dual persistence.

    Configuration Resolution Priority:
    1. Cache (if not expired)
    2. MongoDB symbol-side config
    3. MySQL symbol-side config (future)
    4. MongoDB symbol config
    5. MySQL symbol config (future)
    6. MongoDB global config
    7. MySQL global config (future)
    8. Hardcoded defaults (auto-persisted to MongoDB)
    """

    def __init__(
        self,
        mongodb_client: Optional[MongoDBClient] = None,
        mysql_repository: Optional[MySQLConfigRepository] = None,
        cache_ttl_seconds: int = 60,
    ):
        """
        Initialize configuration manager.

        Args:
            mongodb_client: MongoDB client (primary)
            mysql_repository: MySQL repository (fallback)
            cache_ttl_seconds: Cache TTL in seconds (default: 60)
        """
        self.mongodb_client = mongodb_client
        self.mysql_repository = mysql_repository
        self.cache_ttl_seconds = cache_ttl_seconds

        # Cache: key = f"{symbol or 'global'}:{side or 'all'}", value = (config, timestamp)
        self._cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

        # Background tasks
        self._cache_refresh_task: Optional[asyncio.Task[Any]] = None
        self._running = False

    async def start(self) -> None:
        """Start the configuration manager and background tasks."""
        # Initialize database connections
        if self.mongodb_client:
            await self.mongodb_client.connect()

        if self.mysql_repository:
            await self.mysql_repository.connect()

        # Start cache refresh task
        self._running = True
        self._cache_refresh_task = asyncio.create_task(self._cache_refresh_loop())

        logger.info("Trading configuration manager started")

    async def stop(self) -> None:
        """Stop the configuration manager and clean up."""
        self._running = False

        if self._cache_refresh_task:
            self._cache_refresh_task.cancel()
            try:
                await self._cache_refresh_task
            except asyncio.CancelledError:
                pass

        if self.mongodb_client:
            await self.mongodb_client.disconnect()

        if self.mysql_repository:
            await self.mysql_repository.disconnect()

        logger.info("Trading configuration manager stopped")

    async def _cache_refresh_loop(self) -> None:
        """Background task to refresh cache periodically."""
        while self._running:
            try:
                await asyncio.sleep(self.cache_ttl_seconds)
                # Clear expired cache entries
                current_time = time.time()
                expired_keys = [
                    key
                    for key, (_, timestamp) in self._cache.items()
                    if current_time - timestamp > self.cache_ttl_seconds
                ]
                for key in expired_keys:
                    del self._cache[key]

                if expired_keys:
                    logger.debug(f"Cleared {len(expired_keys)} expired cache entries")
            except Exception as e:
                logger.error(f"Cache refresh loop error: {e}")
                await asyncio.sleep(10)

    def _get_cache_key(self, symbol: Optional[str], side: Optional[str]) -> str:
        """Generate cache key for config lookup."""
        symbol_part = symbol or "global"
        side_part = side or "all"
        return f"{symbol_part}:{side_part}"

    async def get_config(
        self, symbol: Optional[str] = None, side: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get resolved trading configuration.

        Resolution priority:
        1. Cache
        2. MongoDB symbol-side
        3. MongoDB symbol
        4. MongoDB global
        5. Hardcoded defaults

        Args:
            symbol: Trading symbol (None for global)
            side: Position side (None for symbol-level)

        Returns:
            Resolved configuration parameters
        """
        # Check cache first
        cache_key = self._get_cache_key(symbol, side)
        if cache_key in self._cache:
            config, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self.cache_ttl_seconds:
                logger.debug(f"Config cache hit: {cache_key}")
                return config.copy()

        # Start with defaults
        resolved_params = get_default_parameters()

        try:
            # Layer 1: Global config
            if self.mongodb_client and self.mongodb_client.connected:
                global_config = await self.mongodb_client.get_global_config()
                if global_config:
                    resolved_params = merge_parameters(
                        resolved_params, global_config.parameters
                    )
                    logger.debug("Applied global config from MongoDB")

            # Layer 2: Symbol config
            if symbol and self.mongodb_client and self.mongodb_client.connected:
                symbol_config = await self.mongodb_client.get_symbol_config(symbol)
                if symbol_config:
                    resolved_params = merge_parameters(
                        resolved_params, symbol_config.parameters
                    )
                    logger.debug(f"Applied symbol config for {symbol} from MongoDB")

            # Layer 3: Symbol-side config
            if (
                symbol
                and side
                and self.mongodb_client
                and self.mongodb_client.connected
            ):
                symbol_side_config = await self.mongodb_client.get_symbol_side_config(
                    symbol, side
                )
                if symbol_side_config:
                    resolved_params = merge_parameters(
                        resolved_params, symbol_side_config.parameters
                    )
                    logger.debug(
                        f"Applied symbol-side config for {symbol}-{side} from MongoDB"
                    )

        except Exception as e:
            logger.error(f"Error resolving config: {e}")
            # Fall back to defaults

        # Update cache
        self._cache[cache_key] = (resolved_params, time.time())

        return resolved_params.copy()

    async def set_config(
        self,
        parameters: Dict[str, Any],
        changed_by: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        reason: Optional[str] = None,
        validate_only: bool = False,
    ) -> Tuple[bool, Optional[TradingConfig], List[str]]:
        """
        Set trading configuration.

        Args:
            parameters: Configuration parameters to set
            changed_by: Who is making the change
            symbol: Trading symbol (None for global)
            side: Position side (None for symbol-level)
            reason: Reason for the change
            validate_only: If True, only validate without saving

        Returns:
            Tuple of (success, config, errors)
        """
        # Validate parameters
        is_valid, errors = validate_parameters(parameters)
        if not is_valid:
            return False, None, errors

        if validate_only:
            return True, None, []

        try:
            # Determine config type
            if symbol and side:
                config_type = "symbol_side"
            elif symbol:
                config_type = "symbol"
            else:
                config_type = "global"

            # Get existing config for version increment
            existing_config = None
            if config_type == "global":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_global_config()
            elif config_type == "symbol":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_symbol_config(
                        symbol  # type: ignore
                    )
            elif config_type == "symbol_side":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_symbol_side_config(
                        symbol,  # type: ignore
                        side,  # type: ignore
                    )

            # Create new config
            version = (existing_config.version + 1) if existing_config else 1
            now = datetime.utcnow()

            new_config = TradingConfig(
                symbol=symbol,
                side=side,  # type: ignore
                parameters=parameters,
                version=version,
                created_at=existing_config.created_at if existing_config else now,
                updated_at=now,
                created_by=changed_by,
                metadata={},
            )

            # Save to MongoDB
            success = False
            if config_type == "global":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.set_global_config(new_config)
            elif config_type == "symbol":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.set_symbol_config(new_config)
            elif config_type == "symbol_side":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.set_symbol_side_config(
                        new_config
                    )

            if not success:
                return False, None, ["Failed to save configuration"]

            # Create audit record
            audit = TradingConfigAudit(
                config_type=config_type,  # type: ignore
                symbol=symbol,
                side=side,  # type: ignore
                action="update" if existing_config else "create",
                parameters_before=(
                    existing_config.parameters if existing_config else None
                ),
                parameters_after=parameters,
                version_before=existing_config.version if existing_config else None,
                version_after=version,
                changed_by=changed_by,
                reason=reason,
                timestamp=now,
            )

            if self.mongodb_client and self.mongodb_client.connected:
                await self.mongodb_client.add_audit_record(audit)

            # Invalidate cache
            cache_key = self._get_cache_key(symbol, side)
            if cache_key in self._cache:
                del self._cache[cache_key]

            logger.info(
                f"Config updated: {config_type} "
                f"{'(' + symbol + ')' if symbol else ''}"
                f"{'-' + side if side else ''} by {changed_by}"
            )

            return True, new_config, []

        except Exception as e:
            logger.error(f"Error setting config: {e}")
            return False, None, [str(e)]

    async def delete_config(
        self,
        changed_by: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Delete trading configuration.

        Args:
            changed_by: Who is making the change
            symbol: Trading symbol (None for global)
            side: Position side (None for symbol-level)
            reason: Reason for deletion

        Returns:
            Tuple of (success, errors)
        """
        try:
            # Determine config type
            if symbol and side:
                config_type = "symbol_side"
            elif symbol:
                config_type = "symbol"
            else:
                config_type = "global"

            # Get existing config for audit
            existing_config = None
            if config_type == "global":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_global_config()
            elif config_type == "symbol":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_symbol_config(
                        symbol  # type: ignore
                    )
            elif config_type == "symbol_side":
                if self.mongodb_client and self.mongodb_client.connected:
                    existing_config = await self.mongodb_client.get_symbol_side_config(
                        symbol,  # type: ignore
                        side,  # type: ignore
                    )

            # Delete from MongoDB
            success = False
            if config_type == "global":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.delete_global_config()
            elif config_type == "symbol":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.delete_symbol_config(
                        symbol  # type: ignore
                    )
            elif config_type == "symbol_side":
                if self.mongodb_client and self.mongodb_client.connected:
                    success = await self.mongodb_client.delete_symbol_side_config(
                        symbol,  # type: ignore
                        side,  # type: ignore
                    )

            if not success:
                return False, ["Failed to delete configuration"]

            # Create audit record
            if existing_config:
                audit = TradingConfigAudit(
                    config_type=config_type,  # type: ignore
                    symbol=symbol,
                    side=side,  # type: ignore
                    action="delete",
                    parameters_before=existing_config.parameters,
                    parameters_after=None,
                    version_before=existing_config.version,
                    version_after=None,
                    changed_by=changed_by,
                    reason=reason,
                    timestamp=datetime.utcnow(),
                )

                if self.mongodb_client and self.mongodb_client.connected:
                    await self.mongodb_client.add_audit_record(audit)

            # Invalidate cache
            cache_key = self._get_cache_key(symbol, side)
            if cache_key in self._cache:
                del self._cache[cache_key]

            logger.info(
                f"Config deleted: {config_type} "
                f"{'(' + symbol + ')' if symbol else ''}"
                f"{'-' + side if side else ''} by {changed_by}"
            )

            return True, []

        except Exception as e:
            logger.error(f"Error deleting config: {e}")
            return False, [str(e)]

    def invalidate_cache(
        self, symbol: Optional[str] = None, side: Optional[str] = None
    ) -> None:
        """Force cache invalidation for specific config."""
        cache_key = self._get_cache_key(symbol, side)
        if cache_key in self._cache:
            del self._cache[cache_key]
            logger.debug(f"Cache invalidated: {cache_key}")
