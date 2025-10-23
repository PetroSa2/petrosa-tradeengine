"""
Data Manager client for position tracking operations.

This module provides a clean interface for position tracking through the
petrosa-data-manager service, replacing all direct database access.
"""

import logging
from typing import Any, Dict, List, Optional

from tradeengine.services.data_manager_client import DataManagerClient

logger = logging.getLogger(__name__)


class DataManagerPositionClient:
    """
    Data Manager client for position tracking operations.

    This client replaces all direct database access with API calls to
    the petrosa-data-manager service.
    """

    def __init__(self):
        """Initialize the Data Manager position client."""
        self.data_manager_client = DataManagerClient()
        logger.info("Initialized Data Manager position client")

    async def connect(self) -> None:
        """Connect to the Data Manager service."""
        await self.data_manager_client.connect()
        logger.info("Connected to Data Manager service")

    async def disconnect(self) -> None:
        """Disconnect from the Data Manager service."""
        await self.data_manager_client.disconnect()
        logger.info("Disconnected from Data Manager service")

    async def create_position(self, position_data: Dict[str, Any]) -> bool:
        """
        Create a new position record via Data Manager.

        Args:
            position_data: Position data dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use Data Manager to create position
            response = await self.data_manager_client._client.insert_one(
                database="mysql", collection="positions", record=position_data
            )

            if response.get("inserted_id"):
                logger.info(
                    f"✓ Created position record {position_data.get('position_id')} via Data Manager"
                )
                return True
            else:
                logger.error(
                    f"Failed to create position {position_data.get('position_id')} via Data Manager"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to create position via Data Manager: {e}")
            return False

    async def update_position(
        self, position_id: str, update_data: Dict[str, Any]
    ) -> bool:
        """
        Update an existing position record via Data Manager.

        Args:
            position_id: Position ID to update
            update_data: Data to update

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use Data Manager to update position
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"position_id": position_id},
                update={"$set": update_data},
            )

            if response.get("modified_count", 0) > 0:
                logger.info(f"✓ Updated position record {position_id} via Data Manager")
                return True
            else:
                logger.warning(f"No position found to update: {position_id}")
                return False

        except Exception as e:
            logger.error(
                f"Failed to update position {position_id} via Data Manager: {e}"
            )
            return False

    async def update_position_risk_orders(
        self, position_id: str, update_data: Dict[str, Any]
    ) -> bool:
        """
        Update position risk orders via Data Manager.

        Args:
            position_id: Position ID to update
            update_data: Risk order data to update

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use Data Manager to update position risk orders
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"position_id": position_id},
                update={"$set": update_data},
            )

            if response.get("modified_count", 0) > 0:
                logger.info(
                    f"✓ Updated position {position_id} risk orders via Data Manager: {update_data}"
                )
                return True
            else:
                logger.warning(
                    f"No position found to update risk orders: {position_id}"
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to update position risk orders {position_id} via Data Manager: {e}"
            )
            return False

    async def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific position by ID via Data Manager.

        Args:
            position_id: Position ID to retrieve

        Returns:
            Position data dictionary or None if not found
        """
        try:
            # Use Data Manager to get position
            response = await self.data_manager_client._client.query(
                database="mysql",
                collection="positions",
                params={"filter": {"position_id": position_id}, "limit": 1},
            )

            if response and response.get("data"):
                position = response["data"][0]
                logger.info(f"Retrieved position {position_id} via Data Manager")
                return position
            else:
                logger.info(f"Position {position_id} not found via Data Manager")
                return None

        except Exception as e:
            logger.error(f"Failed to get position {position_id} via Data Manager: {e}")
            return None

    async def get_open_positions(
        self, strategy_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all open positions via Data Manager.

        Args:
            strategy_id: Optional strategy ID to filter by

        Returns:
            List of open position dictionaries
        """
        try:
            # Build filter for open positions
            filter_dict = {"status": "open"}
            if strategy_id:
                filter_dict["strategy_id"] = strategy_id

            # Use Data Manager to get open positions
            response = await self.data_manager_client._client.query(
                database="mysql",
                collection="positions",
                params={
                    "filter": filter_dict,
                    "sort_by": "entry_time",
                    "sort_order": "desc",
                },
            )

            positions = response.get("data", []) if response else []
            logger.info(f"Retrieved {len(positions)} open positions via Data Manager")
            return positions

        except Exception as e:
            logger.error(f"Failed to get open positions via Data Manager: {e}")
            return []

    async def upsert_position(self, position_data: Dict[str, Any]) -> bool:
        """
        Upsert a position record via Data Manager.
        
        Args:
            position_data: Position data dictionary
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Use Data Manager to upsert position
            symbol = position_data.get("symbol")
            position_side = position_data.get("position_side", "LONG")
            
            await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"symbol": symbol, "position_side": position_side, "status": "open"},
                update={"$set": position_data},
                upsert=True
            )
            
            logger.info(f"✓ Upserted position {symbol} {position_side} via Data Manager")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upsert position via Data Manager: {e}")
            return False

    async def close_position(
        self, symbol: str, position_side: str, update_data: Dict[str, Any]
    ) -> bool:
        """
        Close a position via Data Manager.
        
        Args:
            symbol: Trading symbol
            position_side: Position side (LONG/SHORT)
            update_data: Data to update for closure
            
        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"symbol": symbol, "position_side": position_side, "status": "open"},
                update={"$set": update_data}
            )
            
            if response.get("modified_count", 0) > 0:
                logger.info(f"✓ Closed position {symbol} {position_side} via Data Manager")
                return True
            else:
                logger.warning(f"No position found to close: {symbol} {position_side}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to close position {symbol} {position_side} via Data Manager: {e}")
            return False

    async def get_daily_pnl(self, date: str) -> Optional[float]:
        """
        Get daily P&L for a specific date via Data Manager.
        
        Args:
            date: Date string in ISO format
            
        Returns:
            Daily P&L value or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mysql",
                collection="daily_pnl",
                params={"filter": {"date": date}, "limit": 1}
            )
            
            if response and response.get("data"):
                daily_pnl = response["data"][0].get("daily_pnl")
                logger.info(f"Retrieved daily P&L for {date} via Data Manager: {daily_pnl}")
                return daily_pnl
            else:
                logger.info(f"No daily P&L found for {date} via Data Manager")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get daily P&L for {date} via Data Manager: {e}")
            return None

    async def update_daily_pnl(self, date: str, daily_pnl: float) -> bool:
        """
        Update daily P&L for a specific date via Data Manager.
        
        Args:
            date: Date string in ISO format
            daily_pnl: Daily P&L value to set
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from datetime import datetime
            
            await self.data_manager_client._client.update_one(
                database="mysql",
                collection="daily_pnl",
                filter={"date": date},
                update={
                    "$set": {
                        "daily_pnl": daily_pnl,
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            
            logger.info(f"✓ Updated daily P&L for {date} via Data Manager: {daily_pnl}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update daily P&L for {date} via Data Manager: {e}")
            return False

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the Data Manager connection.

        Returns:
            Health status dictionary
        """
        try:
            # Check Data Manager health
            health = await self.data_manager_client._client.health()
            return {
                "status": (
                    "healthy" if health.get("status") == "healthy" else "unhealthy"
                ),
                "service": "data-manager",
                "details": health,
            }
        except Exception as e:
            logger.error(f"Data Manager health check failed: {e}")
            return {"status": "unhealthy", "service": "data-manager", "error": str(e)}


# Global Data Manager position client instance
position_client = DataManagerPositionClient()
