"""
Leverage Manager with Hybrid Approach.

Manages leverage configuration for futures trading with:
- Automatic leverage adjustment before trades
- Graceful handling of failures (open positions)
- Status tracking (configured vs actual)
- Manual override capability
"""

import inspect
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from binance import Client
from binance.exceptions import BinanceAPIException
from petrosa_contracts import LeverageStatus

from shared.constants import UTC
from tradeengine.db.mongodb_client import DataManagerConfigClient
from tradeengine.metrics import leverage_change_failures_total, leverage_mismatch

logger = logging.getLogger(__name__)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


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
        binance_client: Client | None = None,
        mongodb_client: DataManagerConfigClient | None = None,
        cache_ttl_minutes: int = 10,
    ):
        """
        Initialize leverage manager.

        Args:
            binance_client: Binance Futures client
            mongodb_client: MongoDB client for persistence
        """
        self.binance_client = binance_client
        self.mongodb_client = mongodb_client
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)

        # In-memory cache of leverage status
        self._leverage_cache: dict[str, LeverageStatus] = {}

    async def ensure_leverage(
        self, symbol: str, target_leverage: int, *, force_refresh: bool = False
    ) -> bool:
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
            # Cache is only an optimization. Refresh before an order when its
            # target differs from the cached target, and whenever the status is stale.
            current_status = await self.get_leverage_status(symbol)
            cache_fresh = bool(
                current_status
                and current_status.last_sync_at
                and datetime.now(UTC) - current_status.last_sync_at < self.cache_ttl
            )
            refresh = force_refresh or not cache_fresh
            if current_status and current_status.configured_leverage != target_leverage:
                refresh = True

            actual_leverage = (
                await self._read_exchange_leverage(symbol) if refresh else None
            )

            # Check if leverage needs update
            if actual_leverage is None and current_status:
                actual_leverage = current_status.actual_leverage
            if actual_leverage == target_leverage:
                leverage_mismatch.labels(symbol=symbol).set(0)
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
                    leverage_mismatch.labels(symbol=symbol).set(0)

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

                    leverage_mismatch.labels(symbol=symbol).set(1)
                    leverage_change_failures_total.labels(
                        symbol=symbol, code=str(getattr(e, "code", "unknown"))
                    ).inc()
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
                leverage_mismatch.labels(symbol=symbol).set(1)
                return False

        except Exception as e:
            logger.error(f"Unexpected error in ensure_leverage for {symbol}: {e}")
            leverage_mismatch.labels(symbol=symbol).set(1)
            return False

    async def _read_exchange_leverage(self, symbol: str) -> int | None:
        """Read leverage from Binance rather than trusting the local cache."""
        if not self.binance_client:
            return None
        try:
            method = getattr(self.binance_client, "futures_symbol_config", None)
            if callable(method):
                response = await _maybe_await(method(symbol=symbol))
            else:
                method = self.binance_client.futures_position_information
                response = await _maybe_await(method(symbol=symbol))
            if isinstance(response, dict):
                response = [response]
            for item in response or []:
                if (
                    item.get("symbol", symbol) == symbol
                    and item.get("leverage") is not None
                ):
                    return int(item["leverage"])
        except Exception as exc:
            logger.warning("Unable to read exchange leverage for %s: %s", symbol, exc)
        return None

    async def reconcile_symbols(
        self, symbols: list[str], target_leverage: int
    ) -> dict[str, Any]:
        """Reconcile the whitelist at startup without blocking service readiness."""
        result: dict[str, Any] = {
            "total": len(symbols),
            "changed": 0,
            "matched": 0,
            "failed": 0,
        }
        for symbol in symbols:
            try:
                actual = await self._read_exchange_leverage(symbol)
                if actual is None:
                    leverage_mismatch.labels(symbol=symbol).set(1)
                    result["failed"] += 1
                elif actual == target_leverage:
                    leverage_mismatch.labels(symbol=symbol).set(0)
                    result["matched"] += 1
                elif await self.ensure_leverage(
                    symbol, target_leverage, force_refresh=True
                ):
                    result["changed"] += 1
                else:
                    result["failed"] += 1
            except Exception as exc:
                logger.warning("Leverage reconciliation failed for %s: %s", symbol, exc)
                leverage_mismatch.labels(symbol=symbol).set(1)
                leverage_change_failures_total.labels(
                    symbol=symbol, code="unknown"
                ).inc()
                result["failed"] += 1
        logger.info("Leverage reconciliation complete: %s", result)
        return result

    async def get_leverage_status(self, symbol: str) -> LeverageStatus | None:
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

    async def force_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
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

    async def sync_all_leverage(self) -> dict[str, Any]:
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

            results: dict[str, Any] = {
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

                symbol_list: list[dict[str, Any]] = results["symbols"]  # type: ignore
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
        actual: int | None,
        success: bool,
        error: str | None,
    ) -> None:
        """Update leverage status in database and cache."""
        try:
            status = LeverageStatus(
                id=None,
                symbol=symbol,
                configured_leverage=configured,
                actual_leverage=actual,
                last_sync_at=datetime.now(UTC),
                last_sync_success=success,
                last_sync_error=error,
                updated_at=datetime.now(UTC),
            )

            # Update cache
            self._leverage_cache[symbol] = status

            # Persist to database
            if self.mongodb_client and self.mongodb_client.connected:
                await self.mongodb_client.set_leverage_status(status)

        except Exception as e:
            logger.error(f"Error updating leverage status for {symbol}: {e}")
