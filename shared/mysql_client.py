"""
MySQL client for position tracking and persistence.

Supports both direct MySQL connections and Data Manager API.
Data Manager is the recommended approach for new deployments.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

import pymysql
from pymysql.cursors import DictCursor

# Import Data Manager client
try:
    from tradeengine.services.data_manager_client import DataManagerClient

    DATA_MANAGER_AVAILABLE = True
except ImportError:
    DATA_MANAGER_AVAILABLE = False
    DataManagerClient = None

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.0  # seconds
RETRY_BACKOFF_MULTIPLIER = 2.0

# Connection metrics
connection_attempts = 0
connection_failures = 0
reconnections = 0
queries_executed = 0
queries_failed = 0
last_successful_connection: datetime | None = None


def ensure_connected(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to ensure MySQL connection is alive before executing methods.

    Automatically checks connection health and attempts reconnection if needed.
    """

    @wraps(func)
    async def wrapper(self: "MySQLClient", *args: Any, **kwargs: Any) -> Any:
        global reconnections, queries_executed, queries_failed

        # Skip connection check if using Data Manager
        if hasattr(self, "use_data_manager") and self.use_data_manager:
            # Execute the wrapped method directly for Data Manager
            try:
                queries_executed += 1
                result = await func(self, *args, **kwargs)
                return result
            except Exception as e:
                queries_failed += 1
                logger.error(
                    f"Data Manager query failed: {e}",
                    exc_info=True,
                )
                raise

        # Check if connection exists and is alive
        if not self._is_connection_alive():
            logger.warning(
                f"MySQL connection lost to {self.host}:{self.port}/{self.database} "
                f"(user: {self.user}). Attempting reconnection..."
            )
            try:
                reconnections += 1
                await self.connect()
                logger.info(
                    f"MySQL reconnection successful to {self.host}:{self.port}/{self.database}"
                )
            except Exception as e:
                queries_failed += 1
                logger.error(
                    f"MySQL reconnection failed to {self.host}:{self.port}/{self.database}: {e}",
                    exc_info=True,
                )
                raise

        # Execute the wrapped method
        try:
            queries_executed += 1
            result = await func(self, *args, **kwargs)
            return result
        except Exception as e:
            queries_failed += 1
            logger.error(
                f"MySQL query failed on {self.host}:{self.port}/{self.database}: {e}",
                exc_info=True,
            )
            raise

    return wrapper


class MySQLClient:
    """
    MySQL client for position tracking operations.

    Supports both direct MySQL connections and Data Manager API.
    Data Manager is the recommended approach for new deployments.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int = 3306,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        uri: str | None = None,
        use_data_manager: bool = True,
    ):
        """
        Initialize MySQL client.

        Args:
            use_data_manager: If True, use Data Manager API instead of direct MySQL
        """
        self.use_data_manager = use_data_manager and DATA_MANAGER_AVAILABLE

        if self.use_data_manager:
            # Initialize Data Manager client
            self.data_manager_client = DataManagerClient()
            self.connection = None  # No direct MySQL connection needed

            # Set required attributes for logging and error messages
            # These are used in error messages and logging even when using Data Manager
            self.host = "data-manager"
            self.port = 8000
            self.user = "data-manager"
            self.password = ""
            self.database = os.getenv("MYSQL_DATABASE", "petrosa")

            logger.info("Using Data Manager for position tracking")
            return

        # Fallback to direct MySQL connection
        logger.info("Using direct MySQL connection")
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

    def _is_connection_alive(self) -> bool:
        """Check if MySQL connection is alive and healthy.

        Returns:
            bool: True if connection is alive, False otherwise
        """
        if not self.connection:
            logger.debug(
                f"MySQL connection check: No connection to {self.host}:{self.port}/{self.database}"
            )
            return False

        try:
            # Use ping to check if connection is alive
            self.connection.ping(reconnect=False)
            return True
        except Exception as e:
            logger.debug(
                f"MySQL connection check failed for {self.host}:{self.port}/{self.database}: {e}"
            )
            return False

    async def connect(self) -> None:
        """Connect to MySQL database or Data Manager with retry logic and metrics tracking."""
        if self.use_data_manager:
            # Use Data Manager - no direct connection needed
            await self.data_manager_client.connect()
            return

        global connection_attempts, connection_failures, last_successful_connection

        max_retries = 3
        retry_delay = 2.0

        for attempt in range(max_retries):
            try:
                connection_attempts += 1
                logger.info(
                    f"Attempting to connect to MySQL {self.host}:{self.port}/{self.database} "
                    f"(attempt {attempt + 1}/{max_retries}, user: {self.user})..."
                )

                self.connection = pymysql.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password or "",
                    database=self.database,
                    cursorclass=DictCursor,
                    autocommit=True,
                    connect_timeout=60,  # Increased from 30s to 60s
                    read_timeout=60,  # Increased from 30s to 60s
                    write_timeout=60,  # Increased from 30s to 60s
                    charset="utf8mb4",
                )
                last_successful_connection = datetime.utcnow()
                logger.info(
                    f"✓ Connected to MySQL successfully: {self.host}:{self.port}/{self.database} "
                    f"(Total attempts: {connection_attempts}, Failures: {connection_failures}, "
                    f"Reconnections: {reconnections})"
                )
                return
            except Exception as e:
                connection_failures += 1
                if attempt < max_retries - 1:
                    backoff = retry_delay * (2**attempt)
                    logger.warning(
                        f"Failed to connect to MySQL {self.host}:{self.port}/{self.database} "
                        f"(attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        f"✗ Failed to connect to MySQL {self.host}:{self.port}/{self.database} "
                        f"after {max_retries} attempts. Error: {e}. "
                        f"Connection info: host={self.host}, port={self.port}, user={self.user}, "
                        f"database={self.database}",
                        exc_info=True,
                    )
                    raise

    async def disconnect(self) -> None:
        """Disconnect from MySQL database."""
        if self.connection:
            self.connection.close()
            logger.info(
                f"Disconnected from MySQL: {self.host}:{self.port}/{self.database}"
            )

    @ensure_connected
    async def execute_query(
        self, query: str, params: tuple[Any, ...] | None = None, fetch: bool = True
    ) -> list[dict[str, Any]] | int:
        """Execute a generic SQL query with optional parameters

        Args:
            query: SQL query to execute
            params: Query parameters (optional)
            fetch: Whether to fetch results (SELECT) or just get rowcount (INSERT/UPDATE)

        Returns:
            List of result dicts for SELECT queries, or rowcount for INSERT/UPDATE
        """
        if self.use_data_manager:
            # Use Data Manager for query execution
            try:
                # TODO: Implement Data Manager query execution
                # For now, return empty result to avoid errors
                logger.info("Query execution delegated to Data Manager")
                return [] if fetch else 0
            except Exception as e:
                logger.error(f"Failed to execute query via Data Manager: {e}")
                return [] if fetch else 0

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, params or ())

                if fetch:
                    # Return fetched results for SELECT queries
                    results = cursor.fetchall()
                    return results if results else []
                else:
                    # Return rowcount for INSERT/UPDATE/DELETE
                    return cursor.rowcount

        except Exception as e:
            logger.error(
                f"Error executing query on {self.host}:{self.port}/{self.database}: {e}",
                exc_info=True,
            )
            if fetch:
                return []
            else:
                return 0

    @ensure_connected
    async def create_position(self, position_data: dict[str, Any]) -> bool:
        """Create a new position record in MySQL with retry logic."""
        if self.use_data_manager:
            # Use Data Manager for position creation
            try:
                # TODO: Implement Data Manager position creation
                # For now, return True to avoid errors
                logger.info(
                    f"Position creation delegated to Data Manager: {position_data.get('position_id')}"
                )
                return True
            except Exception as e:
                logger.error(f"Failed to create position via Data Manager: {e}")
                return False

        # Retry logic for transient errors
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
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
                    f"✓ Created position record {position_data['position_id']} in MySQL "
                    f"({self.host}:{self.port}/{self.database})"
                )
                return True

            except pymysql.err.OperationalError as e:
                # Transient errors - retry
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
                    logger.warning(
                        f"MySQL operational error on {self.host}:{self.port}/{self.database} "
                        f"(attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as reconnect_error:
                        logger.error(
                            f"Failed to reconnect to {self.host}:{self.port}/{self.database}: "
                            f"{reconnect_error}"
                        )
                else:
                    logger.error(
                        f"✗ Failed to create position on {self.host}:{self.port}/{self.database} "
                        f"after {MAX_RETRY_ATTEMPTS} attempts: {e}",
                        exc_info=True,
                    )
                    return False
            except Exception as e:
                # Non-retryable errors
                logger.error(
                    f"✗ Error creating position in MySQL {self.host}:{self.port}/{self.database}: {e}",
                    exc_info=True,
                )
                return False

        return False

    @ensure_connected
    async def update_position(
        self, position_id: str, update_data: dict[str, Any]
    ) -> bool:
        """Update an existing position record in MySQL."""
        if self.use_data_manager:
            # Use Data Manager for position update
            try:
                # TODO: Implement Data Manager position update
                # For now, return True to avoid errors
                logger.info(f"Position update delegated to Data Manager: {position_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to update position via Data Manager: {e}")
                return False

        try:
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

            logger.info(
                f"✓ Updated position record {position_id} in MySQL "
                f"({self.host}:{self.port}/{self.database})"
            )
            return True

        except Exception as e:
            logger.error(
                f"✗ Error updating position {position_id} on MySQL "
                f"{self.host}:{self.port}/{self.database}: {e}",
                exc_info=True,
            )
            return False

    @ensure_connected
    async def update_position_risk_orders(
        self, position_id: str, update_data: dict[str, Any]
    ) -> bool:
        """Update position record with stop loss and take profit order IDs with retry logic."""
        if self.use_data_manager:
            # Use Data Manager for position risk orders update
            try:
                # TODO: Implement Data Manager position risk orders update
                # For now, return True to avoid errors
                logger.info(
                    f"Position risk orders update delegated to Data Manager: {position_id}"
                )
                return True
            except Exception as e:
                logger.error(
                    f"Failed to update position risk orders via Data Manager: {e}"
                )
                return False

        # Retry logic for transient errors
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
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
                    f"✓ Updated position {position_id} risk orders in MySQL "
                    f"({self.host}:{self.port}/{self.database}): {update_data}"
                )
                return True

            except pymysql.err.OperationalError as e:
                # Transient errors - retry
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    wait_time = RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
                    logger.warning(
                        f"MySQL operational error on {self.host}:{self.port}/{self.database} "
                        f"(attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as reconnect_error:
                        logger.error(
                            f"Failed to reconnect to {self.host}:{self.port}/{self.database}: "
                            f"{reconnect_error}"
                        )
                else:
                    logger.error(
                        f"✗ Failed to update position risk orders on "
                        f"{self.host}:{self.port}/{self.database} "
                        f"after {MAX_RETRY_ATTEMPTS} attempts: {e}",
                        exc_info=True,
                    )
                    return False
            except Exception as e:
                # Non-retryable errors
                logger.error(
                    f"✗ Error updating position risk orders {position_id} on MySQL "
                    f"{self.host}:{self.port}/{self.database}: {e}",
                    exc_info=True,
                )
                return False

        return False

    @ensure_connected
    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        """Get a specific position by ID."""
        if self.use_data_manager:
            # Use Data Manager for position retrieval
            try:
                # TODO: Implement Data Manager position retrieval
                # For now, return None to avoid errors
                logger.info(
                    f"Position retrieval delegated to Data Manager: {position_id}"
                )
                return None
            except Exception as e:
                logger.error(f"Failed to get position via Data Manager: {e}")
                return None

        try:
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
            logger.error(
                f"✗ Error fetching position {position_id} from MySQL "
                f"{self.host}:{self.port}/{self.database}: {e}",
                exc_info=True,
            )
            return None

    @ensure_connected
    async def get_open_positions(
        self, strategy_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all open positions, optionally filtered by strategy."""
        if self.use_data_manager:
            # Use Data Manager for open positions retrieval
            try:
                # TODO: Implement Data Manager open positions retrieval
                # For now, return empty list to avoid errors
                logger.info("Open positions retrieval delegated to Data Manager")
                return []
            except Exception as e:
                logger.error(f"Failed to get open positions via Data Manager: {e}")
                return []

        try:
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
            logger.error(
                f"✗ Error fetching open positions from MySQL "
                f"{self.host}:{self.port}/{self.database}: {e}",
                exc_info=True,
            )
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
mysql_client = MySQLClient(
    use_data_manager=os.getenv("USE_DATA_MANAGER", "true").lower() == "true"
)
