"""
Leverage Manager with Hybrid Approach.

Manages leverage configuration for futures trading with:
- Automatic leverage adjustment before trades
- Graceful handling of failures (open positions)
- Status tracking (configured vs actual)
- Manual override capability
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from binance import Client
from binance.exceptions import BinanceAPIException

from contracts.trading_config import LeverageStatus
from tradeengine.db.mongodb_client import DataManagerConfigClient

logger = logging.getLogger(__name__)


class LeverageManager:
    """
    Manages leverage configuration with hybrid approach:
    - Try to set leverage before each trade
    - If fails (open position), log warning and continue
    - Track configured vs actual leverage
    - Provide manual override
    """

    def __init__(
        self,
        binance_client: Optional[Client] = None,
        mongodb_client: Optional[DataManagerConfigClient] = None,
    ):
        """
        Initialize leverage manager.

        Args:
            binance_client: Binance Futures client
            mongodb_client: MongoDB client for persistence
        """
        self.binance_client = binance_client
        self.mongodb_client = mongodb_client

        # In-memory cache of leverage status
        self._leverage_cache: Dict[str, LeverageStatus] = {}

    async def ensure_leverage(self, symbol: str, target_leverage: int) -> bool:
        """
        Ensure symbol has correct leverage before trade.

        Attempts to set leverage if different from target. If fails due to
        open position, logs warning and continues (trading will use existing leverage).

        Args:
            symbol: Trading symbol
            target_leverage: Target leverage to set

        Returns:
            True if leverage matches target, False if mismatch (but not critical)
        """
        try:
            # Get current leverage status
            current_status = await self.get_leverage_status(symbol)

            # Check if leverage needs update
            if current_status and current_status.actual_leverage == target_leverage:
                logger.debug(
                    f"Leverage already correct for {symbol}: {target_leverage}x"
                )
                return True

            # Try to set leverage
            if self.binance_client:
                try:
                    self.binance_client.futures_change_leverage(
                        symbol=symbol, leverage=target_leverage
                    )

                    # Update status
                    await self._update_leverage_status(
                        symbol=symbol,
                        configured=target_leverage,
                        actual=target_leverage,
                        success=True,
                        error=None,
                    )

                    logger.info(f"✓ Leverage set for {symbol}: {target_leverage}x")
                    return True

                except BinanceAPIException as e:
                    # Common error: -4028 = leverage not changed (open position)
                    if e.code == -4028:
                        logger.warning(
                            f"Cannot change leverage for {symbol} (open position exists). "
                            f"Using existing leverage. Target: {target_leverage}x"
                        )
                    else:
                        logger.warning(
                            f"Failed to set leverage for {symbol}: {e.message} "
                            f"(code: {e.code})"
                        )

                    # Update status with failure
                    await self._update_leverage_status(
                        symbol=symbol,
                        configured=target_leverage,
                        actual=(
                            current_status.actual_leverage if current_status else None
                        ),
                        success=False,
                        error=str(e),
                    )

                    # Not critical - trade can continue with existing leverage
                    return False

            else:
                logger.warning("Binance client not available for leverage management")
                return False

        except Exception as e:
            logger.error(f"Unexpected error in ensure_leverage for {symbol}: {e}")
            return False

    async def get_leverage_status(self, symbol: str) -> Optional[LeverageStatus]:
        """
        Get leverage status for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            LeverageStatus or None if not found
        """
        # Check cache first
        if symbol in self._leverage_cache:
            return self._leverage_cache[symbol]

        # Load from database
        if self.mongodb_client and self.mongodb_client.connected:
            status = await self.mongodb_client.get_leverage_status(symbol)
            if status:
                self._leverage_cache[symbol] = status
                return status

        return None

    async def force_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """
        Manually force leverage change (admin operation).

        Args:
            symbol: Trading symbol
            leverage: Target leverage

        Returns:
            Result dictionary with status
        """
        try:
            if not self.binance_client:
                return {"success": False, "error": "Binance client not available"}

            # Try to set leverage
            self.binance_client.futures_change_leverage(
                symbol=symbol, leverage=leverage
            )

            # Update status
            await self._update_leverage_status(
                symbol=symbol,
                configured=leverage,
                actual=leverage,
                success=True,
                error=None,
            )

            logger.info(f"✓ Leverage force-set for {symbol}: {leverage}x")

            return {
                "success": True,
                "symbol": symbol,
                "leverage": leverage,
                "message": "Leverage successfully set",
            }

        except BinanceAPIException as e:
            logger.error(f"Failed to force leverage for {symbol}: {e.message}")
            return {"success": False, "error": f"{e.message} (code: {e.code})"}

    async def sync_all_leverage(self) -> Dict[str, Any]:
        """
        Sync leverage for all configured symbols at startup.

        Returns:
            Summary of sync operation
        """
        if not self.mongodb_client or not self.mongodb_client.connected:
            return {"success": False, "error": "MongoDB not connected"}

        try:
            # Get all leverage status records
            all_status = await self.mongodb_client.get_all_leverage_status()

            results: Dict[str, Any] = {
                "total": len(all_status),
                "synced": 0,
                "failed": 0,
                "symbols": [],
            }

            for status in all_status:
                success = await self.ensure_leverage(
                    status.symbol, status.configured_leverage
                )

                if success:
                    results["synced"] = results["synced"] + 1  # type: ignore
                else:
                    results["failed"] = results["failed"] + 1  # type: ignore

                symbol_list: List[Dict[str, Any]] = results["symbols"]  # type: ignore
                symbol_list.append(
                    {
                        "symbol": status.symbol,
                        "target": status.configured_leverage,
                        "success": success,
                    }
                )

            logger.info(
                f"Leverage sync complete: {results['synced']} synced, "
                f"{results['failed']} failed"
            )

            return results

        except Exception as e:
            logger.error(f"Error syncing all leverage: {e}")
            return {"success": False, "error": str(e)}

    async def _update_leverage_status(
        self,
        symbol: str,
        configured: int,
        actual: Optional[int],
        success: bool,
        error: Optional[str],
    ) -> None:
        """Update leverage status in database and cache."""
        try:
            status = LeverageStatus(
                id=None,
                symbol=symbol,
                configured_leverage=configured,
                actual_leverage=actual,
                last_sync_at=datetime.utcnow(),
                last_sync_success=success,
                last_sync_error=error,
                updated_at=datetime.utcnow(),
            )

            # Update cache
            self._leverage_cache[symbol] = status

            # Persist to database
            if self.mongodb_client and self.mongodb_client.connected:
                await self.mongodb_client.set_leverage_status(status)

        except Exception as e:
            logger.error(f"Error updating leverage status for {symbol}: {e}")
