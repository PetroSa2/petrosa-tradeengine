"""
MySQL client for position tracking and persistence.
"""

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.0  # seconds
RETRY_BACKOFF_MULTIPLIER = 2.0


class MySQLClient:
    """MySQL client for position tracking operations."""

    def __init__(
        self,
        host: str | None = None,
        port: int = 3306,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        uri: str | None = None,
    ):
        """Initialize MySQL client."""
        # Try to get URI from environment first
        mysql_uri = uri or os.getenv("MYSQL_URI")

        if mysql_uri:
            # Parse the URI - handle mysql+pymysql:// protocol
            if mysql_uri.startswith("mysql+pymysql://"):
                # Remove the mysql+pymysql:// prefix for parsing
                uri_for_parsing = mysql_uri.replace("mysql+pymysql://", "mysql://")
            else:
                uri_for_parsing = mysql_uri

            parsed_uri = urlparse(uri_for_parsing)

            self.host: str = parsed_uri.hostname or "localhost"
            self.port: int = parsed_uri.port or 3306
            self.user: str = parsed_uri.username or "root"
            # URL decode the password to handle special characters
            self.password: str | None = parsed_uri.password
            if self.password and "%" in self.password:
                from urllib.parse import unquote

                self.password = unquote(self.password)
            # Handle URL encoding in database name
            self.database: str = parsed_uri.path.lstrip("/")
            if "%" in self.database:
                from urllib.parse import unquote

                self.database = unquote(self.database)
            logger.info("MySQL URI parsed successfully")
        else:
            # Use individual parameters
            self.host = (
                host or os.getenv("MYSQL_HOST", "mysql-server")
            ) or "mysql-server"
            self.port = port or int(os.getenv("MYSQL_PORT", "3306"))
            self.user = (user or os.getenv("MYSQL_USER", "petrosa")) or "petrosa"
            self.password = password or os.getenv("MYSQL_PASSWORD", "petrosa")
            self.database = (
                database or os.getenv("MYSQL_DATABASE", "petrosa")
            ) or "petrosa"

        self.connection: Any = None

    async def connect(self) -> None:
        """Connect to MySQL database."""
        try:
            logger.info("Attempting to connect to MySQL...")

            self.connection = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password or "",
                database=self.database,
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=30,
                read_timeout=30,
                write_timeout=30,
                charset="utf8mb4",
            )
            logger.info("Connected to MySQL successfully")
        except Exception as e:
            logger.error(f"Failed to connect to MySQL: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from MySQL database."""
        if self.connection:
            self.connection.close()
            logger.info("Disconnected from MySQL")

    async def create_position(self, position_data: dict[str, Any]) -> bool:
        """Create a new position record in MySQL with retry logic."""
        if not self.connection:
            logger.error("Not connected to MySQL")
            return False

        # Retry logic for transient errors
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                # Check if connection is still alive
                try:
                    self.connection.ping(reconnect=True)
                except Exception as e:
                    logger.warning(f"Connection lost, reconnecting: {e}")
                    await self.connect()

                sql = """
                    INSERT INTO positions (
                        position_id, strategy_id, exchange, symbol, position_side,
                        entry_price, quantity, entry_time, stop_loss, take_profit,
                        status, metadata, exchange_position_id, entry_order_id,
                        entry_trade_ids, stop_loss_order_id, take_profit_order_id,
                        commission_asset, commission_total
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """

                # Serialize JSON fields
                metadata_json = json.dumps(position_data.get("metadata", {}))
                entry_trade_ids_json = json.dumps(
                    position_data.get("entry_trade_ids", [])
                )

                with self.connection.cursor() as cursor:
                    cursor.execute(
                        sql,
                        (
                            position_data["position_id"],
                            position_data["strategy_id"],
                            position_data.get("exchange", "binance"),
                            position_data["symbol"],
                            position_data["position_side"],
                            position_data["entry_price"],
                            position_data["quantity"],
                            position_data["entry_time"],
                            position_data.get("stop_loss"),
                            position_data.get("take_profit"),
                            position_data.get("status", "open"),
                            metadata_json,
                            position_data.get("exchange_position_id"),
                            position_data.get("entry_order_id"),
                            entry_trade_ids_json,
                            position_data.get("stop_loss_order_id"),
                            position_data.get("take_profit_order_id"),
                            position_data.get("commission_asset"),
                            position_data.get("commission_total"),
                        ),
                    )

                logger.info(
                    f"Created position record {position_data['position_id']} in MySQL"
                )
                return True

            except pymysql.err.OperationalError as e:
                # Transient errors - retry
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
                    logger.warning(
                        f"MySQL operational error on attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as reconnect_error:
                        logger.error(f"Failed to reconnect: {reconnect_error}")
                else:
                    logger.error(
                        f"Failed to create position after {MAX_RETRY_ATTEMPTS} attempts: {e}"
                    )
                    return False
            except Exception as e:
                # Non-retryable errors
                logger.error(f"Error creating position in MySQL: {e}")
                return False

        return False

    async def update_position(
        self, position_id: str, update_data: dict[str, Any]
    ) -> bool:
        """Update an existing position record in MySQL."""
        if not self.connection:
            logger.error("Not connected to MySQL")
            return False

        try:
            # Check if connection is still alive
            try:
                self.connection.ping(reconnect=True)
            except Exception as e:
                logger.warning(f"Connection lost, reconnecting: {e}")
                await self.connect()

            # Build dynamic UPDATE query based on provided fields
            set_clauses = []
            values = []

            for key, value in update_data.items():
                if key == "exit_trade_ids" or key == "metadata":
                    # JSON fields need serialization
                    set_clauses.append(f"{key} = %s")
                    values.append(json.dumps(value))
                else:
                    set_clauses.append(f"{key} = %s")
                    values.append(value)

            # Add position_id for WHERE clause
            values.append(position_id)

            sql = f"""
                UPDATE positions
                SET {', '.join(set_clauses)}
                WHERE position_id = %s
            """

            with self.connection.cursor() as cursor:
                cursor.execute(sql, values)

            logger.info(f"Updated position record {position_id} in MySQL")
            return True

        except Exception as e:
            logger.error(f"Error updating position {position_id} in MySQL: {e}")
            # Try to reconnect on error
            try:
                await self.connect()
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect: {reconnect_error}")
            return False

    async def update_position_risk_orders(
        self, position_id: str, update_data: dict[str, Any]
    ) -> bool:
        """Update position record with stop loss and take profit order IDs with retry logic."""
        if not self.connection:
            logger.error("Not connected to MySQL")
            return False

        # Retry logic for transient errors
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                # Check if connection is still alive
                try:
                    self.connection.ping(reconnect=True)
                except Exception as e:
                    logger.warning(f"Connection lost, reconnecting: {e}")
                    await self.connect()

                # Build dynamic UPDATE query for risk order fields
                set_clauses = []
                values = []

                for key, value in update_data.items():
                    if key in ["stop_loss_order_id", "take_profit_order_id"]:
                        set_clauses.append(f"{key} = %s")
                        values.append(value)

                if not set_clauses:
                    logger.warning("No risk order fields to update")
                    return True

                # Add position_id for WHERE clause
                values.append(position_id)

                sql = f"""
                    UPDATE positions
                    SET {', '.join(set_clauses)}
                    WHERE position_id = %s
                """

                with self.connection.cursor() as cursor:
                    cursor.execute(sql, values)

                logger.info(
                    f"Updated position {position_id} risk orders in MySQL: {update_data}"
                )
                return True

            except pymysql.err.OperationalError as e:
                # Transient errors - retry
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
                    logger.warning(
                        f"MySQL operational error on attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as reconnect_error:
                        logger.error(f"Failed to reconnect: {reconnect_error}")
                else:
                    logger.error(
                        f"Failed to update position risk orders after {MAX_RETRY_ATTEMPTS} attempts: {e}"
                    )
                    return False
            except Exception as e:
                # Non-retryable errors
                logger.error(
                    f"Error updating position risk orders {position_id} in MySQL: {e}"
                )
                return False

        return False

    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        """Get a specific position by ID."""
        if not self.connection:
            logger.error("Not connected to MySQL")
            return None

        try:
            # Check if connection is still alive
            try:
                self.connection.ping(reconnect=True)
            except Exception as e:
                logger.warning(f"Connection lost, reconnecting: {e}")
                await self.connect()

            sql = """
                SELECT * FROM positions WHERE position_id = %s
            """

            with self.connection.cursor() as cursor:
                cursor.execute(sql, (position_id,))
                result = cursor.fetchone()

                if result:
                    # Parse JSON fields
                    if result.get("metadata"):
                        result["metadata"] = json.loads(result["metadata"])
                    if result.get("entry_trade_ids"):
                        result["entry_trade_ids"] = json.loads(
                            result["entry_trade_ids"]
                        )
                    if result.get("exit_trade_ids"):
                        result["exit_trade_ids"] = json.loads(result["exit_trade_ids"])

                    # Convert Decimal to float
                    for key in [
                        "entry_price",
                        "quantity",
                        "stop_loss",
                        "take_profit",
                        "commission_total",
                        "exit_price",
                        "pnl",
                        "pnl_pct",
                        "pnl_after_fees",
                        "final_commission",
                    ]:
                        if result.get(key) is not None:
                            result[key] = float(result[key])

                return result

        except Exception as e:
            logger.error(f"Error fetching position {position_id} from MySQL: {e}")
            return None

    async def get_open_positions(
        self, strategy_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all open positions, optionally filtered by strategy."""
        if not self.connection:
            logger.error("Not connected to MySQL")
            return []

        try:
            # Check if connection is still alive
            try:
                self.connection.ping(reconnect=True)
            except Exception as e:
                logger.warning(f"Connection lost, reconnecting: {e}")
                await self.connect()

            if strategy_id:
                sql = """
                    SELECT * FROM positions
                    WHERE status = 'open' AND strategy_id = %s
                    ORDER BY entry_time DESC
                """
                query_params: tuple[str, ...] = (strategy_id,)
            else:
                sql = """
                    SELECT * FROM positions
                    WHERE status = 'open'
                    ORDER BY entry_time DESC
                """
                query_params = ()

            with self.connection.cursor() as cursor:
                cursor.execute(sql, query_params)
                results = cursor.fetchall()

                # Parse JSON fields and convert Decimal to float
                for result in results:
                    if result.get("metadata"):
                        result["metadata"] = json.loads(result["metadata"])
                    if result.get("entry_trade_ids"):
                        result["entry_trade_ids"] = json.loads(
                            result["entry_trade_ids"]
                        )

                    for key in [
                        "entry_price",
                        "quantity",
                        "stop_loss",
                        "take_profit",
                        "commission_total",
                    ]:
                        if result.get(key) is not None:
                            result[key] = float(result[key])

                return results

        except Exception as e:
            logger.error(f"Error fetching open positions from MySQL: {e}")
            return []

    async def health_check(self) -> dict[str, Any]:
        """Check MySQL connection health."""
        if not self.connection:
            return {"status": "disconnected", "error": "No connection"}

        try:
            self.connection.ping()
            return {
                "status": "healthy",
                "host": self.host,
                "database": self.database,
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


# Global MySQL client instance
mysql_client = MySQLClient()
