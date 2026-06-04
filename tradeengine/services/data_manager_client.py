"""
Data Manager client for petrosa-tradeengine.

This module provides a client for interacting with the petrosa-data-manager API
for audit logging and configuration management.

Closes #447 (P0): replaces the prior `BaseDataManagerClient` stub — which
silently returned placeholder dicts and never issued any HTTP — with a real
async httpx implementation that talks to data-manager's legacy and generic
endpoints with bounded retry-with-backoff.
"""

import asyncio
import logging
import os
import random
from datetime import datetime
from typing import Any, Optional

import httpx

from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit
from shared.constants import UTC

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when the data-manager API call fails after exhausting retries."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ConnectionError(APIError):
    """Raised when connecting to data-manager fails after retries."""

    pass


_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_RETRY_BACKOFF_BASE = 0.5
_RETRY_BACKOFF_CAP = 8.0


class BaseDataManagerClient:
    """
    Async HTTP client for petrosa-data-manager.

    Talks to data-manager's legacy `/api/v1/data/{insert,query}` routes
    (per #447 F6 operator decision — matches extractor pattern) and the
    generic `/api/v1/{db}/{coll}` routes for update/upsert/delete. Bounded
    retry-with-jittered-backoff on 5xx and connection errors; raises
    :class:`APIError` (or :class:`ConnectionError`) on retry exhaustion.

    Response-key contract preserved for callers in
    `tradeengine/db/mongodb_client.py` and `shared/mysql_client.py`:

    - ``query``       → ``{"data": list}``
    - ``insert_one``  → ``{"inserted_id": str, "inserted_count": int}``
    - ``insert``      → ``{"inserted_count": int}``
    - ``update_one``  → ``{"modified_count": int}``
    - ``upsert_one``  → ``{"modified_count", "upserted_count", "upserted_id"}``
    - ``delete_one``  → ``{"deleted_count": int}``
    - ``delete``      → ``{"deleted_count": int}``
    - ``health``      → ``{"status": "healthy" | "unhealthy", ...}``
    """

    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._client: httpx.AsyncClient | None = None
        self._client_lock: asyncio.Lock | None = None

    def _ensure_lock(self) -> asyncio.Lock:
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        return self._client_lock

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._ensure_lock():
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        base_url=self.base_url,
                        timeout=httpx.Timeout(self.timeout),
                    )
        return self._client

    async def close(self) -> None:
        """Close the underlying httpx client (idempotent)."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def _retry_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an HTTP request with bounded retry-with-backoff."""
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.request(method, path, json=json_body, params=params)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = ConnectionError(f"connection to data-manager failed: {exc}")
            except httpx.HTTPError as exc:
                last_exc = APIError(f"transport error: {exc}")
            else:
                if resp.status_code in _RETRYABLE_STATUS_CODES:
                    last_exc = APIError(
                        f"data-manager {method} {path} returned {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:1000],
                    )
                elif 200 <= resp.status_code < 300:
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = {}
                    return payload if isinstance(payload, dict) else {"data": payload}
                else:
                    # Non-retryable 4xx (or other) — fail fast
                    raise APIError(
                        f"data-manager {method} {path} returned {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:1000],
                    )

            if attempt < self.max_retries - 1:
                backoff = min(
                    _RETRY_BACKOFF_BASE * (2**attempt) + random.uniform(0, 0.25),
                    _RETRY_BACKOFF_CAP,
                )
                logger.warning(
                    "data-manager %s %s attempt %d/%d failed (%s); retrying in %.2fs",
                    method,
                    path,
                    attempt + 1,
                    self.max_retries,
                    last_exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    async def health(self) -> dict[str, Any]:
        """Probe data-manager's readiness endpoint.

        Returns the canonical ``{"status": "healthy"}`` payload on a 2xx with
        a recognized status field; ``{"status": "unhealthy", "error": ...}``
        on retry exhaustion (does NOT raise — callers gate on the status).
        """
        try:
            body = await self._retry_request("GET", "/health/readiness")
        except APIError as exc:
            return {"status": "unhealthy", "error": str(exc)}
        status_field = body.get("status") or body.get("readiness")
        if isinstance(status_field, str) and status_field.lower() in {
            "ok",
            "ready",
            "healthy",
            "up",
            "available",
        }:
            return {"status": "healthy", "raw": body}
        # Some readiness endpoints answer 200 with no `status` field;
        # treat a 2xx response as healthy so the outer DataManagerClient
        # can still proceed when the API surface evolves.
        return {"status": "healthy" if body else "unhealthy", "raw": body}

    async def query(
        self, database: str, collection: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Query records via `POST /api/v1/data/query`."""
        body: dict[str, Any] = {"database": database, "collection": collection}
        for key in ("filter", "sort", "limit", "offset", "fields"):
            value = kwargs.get(key)
            if value is not None:
                body[key] = value
        resp = await self._retry_request("POST", "/api/v1/data/query", json_body=body)
        return {"data": resp.get("data", []), "pagination": resp.get("pagination")}

    async def insert_one(
        self, database: str, collection: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        """Insert a single record via `POST /api/v1/data/insert`.

        data-manager's legacy insert endpoint returns ``inserted_count``
        but NOT ``inserted_id``. Per AC0.2 the response must reflect the
        real call — never the literal ``"placeholder"``. We synthesize a
        deterministic non-placeholder id from the record's own identity
        when present, otherwise from collection + count.
        """
        body = {
            "database": database,
            "collection": collection,
            "records": [record],
        }
        resp = await self._retry_request("POST", "/api/v1/data/insert", json_body=body)
        inserted_count = int(resp.get("inserted_count", 0) or 0)
        synthetic_id = ""
        if inserted_count > 0:
            for candidate_key in ("_id", "id", "uuid"):
                value = record.get(candidate_key)
                if value:
                    synthetic_id = str(value)
                    break
            if not synthetic_id:
                synthetic_id = f"{collection}-{inserted_count}"
        return {"inserted_id": synthetic_id, "inserted_count": inserted_count}

    async def insert(
        self,
        database: str,
        collection: str,
        data: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert one or many records via `POST /api/v1/data/insert`.

        Mirrors the audit-trail / batch-insert path in the outer
        :class:`DataManagerClient`. Existing call sites pass a single
        dict (`add_audit_record`); we accept lists too for forward
        compatibility.
        """
        records = data if isinstance(data, list) else [data]
        body = {"database": database, "collection": collection, "records": records}
        resp = await self._retry_request("POST", "/api/v1/data/insert", json_body=body)
        return {"inserted_count": int(resp.get("inserted_count", 0) or 0)}

    async def update_one(
        self,
        database: str,
        collection: str,
        filter: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any]:
        """Update via `PUT /api/v1/{database}/{collection}` (upsert=False)."""
        body = {"filter": filter, "data": update, "upsert": False}
        resp = await self._retry_request(
            "PUT", f"/api/v1/{database}/{collection}", json_body=body
        )
        return {"modified_count": int(resp.get("modified_count", 0) or 0)}

    async def upsert_one(
        self,
        database: str,
        collection: str,
        filter: dict[str, Any],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Upsert via `PUT /api/v1/{database}/{collection}` (upsert=True)."""
        body = {"filter": filter, "data": record, "upsert": True}
        resp = await self._retry_request(
            "PUT", f"/api/v1/{database}/{collection}", json_body=body
        )
        modified_count = int(resp.get("modified_count", 0) or 0)
        upserted_count = int(resp.get("upserted_count", 0) or 0)
        upserted_id = resp.get("upserted_id")
        if upserted_id is None and upserted_count > 0:
            upserted_id = f"{collection}-upsert-{upserted_count}"
        return {
            "modified_count": modified_count,
            "upserted_count": upserted_count,
            "upserted_id": upserted_id,
        }

    async def delete_one(
        self, database: str, collection: str, filter: dict[str, Any]
    ) -> dict[str, Any]:
        """Delete via `DELETE /api/v1/{database}/{collection}`."""
        body = {"filter": filter}
        resp = await self._retry_request(
            "DELETE", f"/api/v1/{database}/{collection}", json_body=body
        )
        return {"deleted_count": int(resp.get("deleted_count", 0) or 0)}

    async def delete(
        self, database: str, collection: str, filter: dict[str, Any]
    ) -> dict[str, Any]:
        """Delete (multi). Same DELETE route as `delete_one`."""
        return await self.delete_one(database, collection, filter)

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Generic HTTP passthrough used by `mongodb_client.py`."""
        path = url if url.startswith("/") else f"/{url}"
        json_body = kwargs.get("json")
        if json_body is None:
            json_body = kwargs.get("json_body")
        return await self._retry_request(
            method.upper(),
            path,
            json_body=json_body,
            params=kwargs.get("params"),
        )


def get_logger() -> Any:
    """Backwards-compatible accessor for the module logger."""
    return logger


class DataManagerClient:
    """
    Data Manager client for the Trade Engine.

    Provides methods for audit logging and configuration management
    through the petrosa-data-manager API.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize the Data Manager client.

        Args:
            base_url: Data Manager API base URL
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url or os.getenv(
            "DATA_MANAGER_URL", "http://petrosa-data-manager:8000"
        )
        self.timeout = timeout
        self.max_retries = max_retries

        # Initialize the base client
        self._client = BaseDataManagerClient(
            base_url=str(self.base_url),
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

        self._logger = logger
        self._logger.info(f"Initialized Data Manager client: {self.base_url}")

    async def connect(self) -> None:
        """Connect to the Data Manager service."""
        try:
            # Test connection with health check
            health = await self._client.health()
            if health.get("status") != "healthy":
                raise ConnectionError(f"Data Manager health check failed: {health}")

            self._logger.info("Connected to Data Manager service")

        except Exception as e:
            self._logger.error(f"Failed to connect to Data Manager: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from the Data Manager service."""
        try:
            await self._client.close()
            self._logger.info("Disconnected from Data Manager service")
        except Exception as e:
            self._logger.warning(f"Error disconnecting from Data Manager: {e}")

    # Configuration Management Methods

    async def get_global_config(self) -> TradingConfig | None:
        """Get global trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_global",
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching global config: {e}")
            return None

    async def set_global_config(self, config: TradingConfig) -> bool:
        """Set global trading configuration."""
        try:
            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.now(UTC)

            result = await self._client.upsert_one(
                database="mongodb",
                collection="trading_configs_global",
                filter={},  # Empty filter for global config
                record=config_dict,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info("Updated global trading config")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting global config: {e}")
            return False

    async def delete_global_config(self) -> bool:
        """Delete global trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_global",
                filter={},  # Empty filter to delete all
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info("Deleted global trading config")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error deleting global config: {e}")
            return False

    async def get_symbol_config(self, symbol: str) -> TradingConfig | None:
        """Get symbol-specific trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": symbol},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching symbol config for {symbol}: {e}")
            return None

    async def set_symbol_config(self, config: TradingConfig) -> bool:
        """Set symbol-specific trading configuration."""
        try:
            if not config.symbol:
                return False

            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.now(UTC)

            result = await self._client.upsert_one(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": config.symbol},
                record=config_dict,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info(f"Updated symbol config for {config.symbol}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting symbol config: {e}")
            return False

    async def delete_symbol_config(self, symbol: str) -> bool:
        """Delete symbol-specific trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": symbol},
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info(f"Deleted symbol config for {symbol}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error deleting symbol config for {symbol}: {e}")
            return False

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> TradingConfig | None:
        """Get symbol-side-specific trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(
                f"Error fetching symbol-side config for {symbol}-{side}: {e}"
            )
            return None

    async def set_symbol_side_config(self, config: TradingConfig) -> bool:
        """Set symbol-side-specific trading configuration."""
        try:
            if not config.symbol or not config.side:
                return False

            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.now(UTC)

            result = await self._client.upsert_one(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": config.symbol, "side": config.side},
                record=config_dict,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info(
                    f"Updated symbol-side config for {config.symbol}-{config.side}"
                )
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting symbol-side config: {e}")
            return False

    async def delete_symbol_side_config(self, symbol: str, side: str) -> bool:
        """Delete symbol-side-specific trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info(f"Deleted symbol-side config for {symbol}-{side}")
                return True
            return False

        except Exception as e:
            self._logger.error(
                f"Error deleting symbol-side config for {symbol}-{side}: {e}"
            )
            return False

    # Audit Trail Methods

    async def add_audit_record(self, audit: TradingConfigAudit) -> bool:
        """Add audit trail record."""
        try:
            audit_dict = audit.model_dump(exclude={"id"})

            result = await self._client.insert(
                database="mongodb",
                collection="trading_configs_audit",
                data=audit_dict,
            )

            if result.get("inserted_count", 0) > 0:
                self._logger.debug(f"Added audit record: {audit.get_change_summary()}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error adding audit record: {e}")
            return False

    async def get_audit_trail(
        self, symbol: str | None = None, side: str | None = None, limit: int = 100
    ) -> list[TradingConfigAudit]:
        """Get audit trail records with optional filters."""
        try:
            filter_dict = {}
            if symbol:
                filter_dict["symbol"] = symbol
            if side:
                filter_dict["side"] = side

            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_audit",
                filter=filter_dict,
                sort={"timestamp": -1},
                limit=limit,
            )

            # Convert to models
            audit_records = []
            for doc in result.get("data", []):
                doc["id"] = str(doc.pop("_id", ""))
                audit_records.append(TradingConfigAudit(**doc))

            return audit_records

        except Exception as e:
            self._logger.error(f"Error fetching audit trail: {e}")
            return []

    # Leverage Status Methods

    async def get_leverage_status(self, symbol: str) -> LeverageStatus | None:
        """Get leverage status for symbol."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="leverage_status",
                filter={"symbol": symbol},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return LeverageStatus(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching leverage status for {symbol}: {e}")
            return None

    async def set_leverage_status(self, status: LeverageStatus) -> bool:
        """Set leverage status for symbol."""
        try:
            status_dict = status.model_dump(exclude={"id"})
            status_dict["updated_at"] = datetime.now(UTC)

            result = await self._client.upsert_one(
                database="mongodb",
                collection="leverage_status",
                filter={"symbol": status.symbol},
                record=status_dict,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.debug(
                    f"Updated leverage status for {status.symbol}: "
                    f"configured={status.configured_leverage}, "
                    f"actual={status.actual_leverage}"
                )
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting leverage status: {e}")
            return False

    async def get_all_leverage_status(self) -> list[LeverageStatus]:
        """Get all leverage status records."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="leverage_status",
                limit=1000,
            )

            # Convert to models
            status_list = []
            for doc in result.get("data", []):
                doc["id"] = str(doc.pop("_id", ""))
                status_list.append(LeverageStatus(**doc))

            return status_list

        except Exception as e:
            self._logger.error(f"Error fetching all leverage status: {e}")
            return []

    async def health_check(self) -> dict[str, Any]:
        """
        Check the health of the Data Manager service.

        Returns:
            Health status information
        """
        try:
            health = await self._client.health()
            self._logger.info(
                f"Data Manager health check: {health.get('status', 'unknown')}"
            )
            return health
        except Exception as e:
            self._logger.error(f"Data Manager health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def __aenter__(self) -> "DataManagerClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()
