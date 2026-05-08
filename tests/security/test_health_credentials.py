"""
Security regression: health/readiness endpoints must never expose raw credentials.

Patterns checked: passwords embedded in MongoDB URIs (mongodb://user:pass@host),
MySQL URIs (mysql+pymysql://user:pass@host), and bare secret strings.
"""

import re

import pytest

SECRET_PATTERNS = [
    re.compile(r"mongodb(?:\+srv)?://[^:@/]+:[^@/]+@"),  # mongodb://user:pass@
    re.compile(r"mysql(?:\+\w+)?://[^:@/]+:[^@/]+@"),  # mysql://user:pass@
]


def _assert_no_secrets(payload: object, path: str = "root") -> None:
    """Recursively walk a dict/list and fail if any string value matches a secret pattern."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            _assert_no_secrets(v, path=f"{path}.{k}")
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            _assert_no_secrets(v, path=f"{path}[{i}]")
    elif isinstance(payload, str):
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(payload), (
                f"Credential leak detected at '{path}': value matches secret pattern {pattern.pattern!r}"
            )


class TestRedactUri:
    def test_mongodb_uri_credentials_stripped(self) -> None:
        from shared.constants import redact_uri

        raw = "mongodb+srv://admin:s3cr3t@cluster.mongodb.net/db"
        result = redact_uri(raw)
        assert "s3cr3t" not in result
        assert "admin" not in result
        assert "cluster.mongodb.net" in result
        assert result == "mongodb+srv://***@cluster.mongodb.net/db"

    def test_plain_mongodb_uri(self) -> None:
        from shared.constants import redact_uri

        raw = "mongodb://user:pass@localhost:27017/mydb"
        result = redact_uri(raw)
        assert "pass" not in result
        assert "user" not in result
        assert "localhost:27017" in result

    def test_mysql_uri_credentials_stripped(self) -> None:
        from shared.constants import redact_uri

        raw = "mysql+pymysql://petrosa:secret123@db.host:3306/petrosa"
        result = redact_uri(raw)
        assert "secret123" not in result
        assert "petrosa:" not in result
        assert "db.host" in result

    def test_none_returns_empty_string(self) -> None:
        from shared.constants import redact_uri

        assert redact_uri(None) == ""

    def test_empty_string_returns_empty_string(self) -> None:
        from shared.constants import redact_uri

        assert redact_uri("") == ""

    def test_empty_password_in_uri_masked(self) -> None:
        from shared.constants import redact_uri

        raw = "mongodb://user:@localhost:27017/mydb"
        result = redact_uri(raw)
        assert "user" not in result
        assert result == "mongodb://***@localhost:27017/mydb"

    def test_uri_without_credentials_unchanged(self) -> None:
        from shared.constants import redact_uri

        raw = "mongodb+srv://cluster.mongodb.net/db"
        result = redact_uri(raw)
        assert result == raw


class TestPositionManagerHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_no_credential_leak(self) -> None:
        from unittest.mock import MagicMock, patch

        from tradeengine.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm.positions = {}
        pm.last_sync_time = None
        pm.mongodb_db = None
        pm.settings = MagicMock()
        pm.settings.mongodb_uri = "mongodb+srv://admin:topsecret@host/db"

        with patch(
            "tradeengine.position_manager.get_mongodb_connection_string",
            return_value="",
        ):
            result = await pm.health_check()

        _assert_no_secrets(result)
        assert "mongodb_uri" in result
        assert "topsecret" not in str(result)


class TestDistributedLockHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_no_credential_leak(self) -> None:
        from unittest.mock import MagicMock, patch

        from shared.distributed_lock import DistributedLockManager

        lock = DistributedLockManager.__new__(DistributedLockManager)
        lock.lock_name = "test-lock"
        lock.lock_id = "abc123"
        lock.collection = None
        lock.mongodb_db = None
        lock.pod_id = "pod-abc"
        lock.is_leader = False
        lock.lock_timeout = 30
        lock.heartbeat_interval = 10
        lock.settings = MagicMock()
        lock.settings.mongodb_uri = "mongodb+srv://user:hunter2@mongo.host/petrosa"

        with (
            patch(
                "shared.distributed_lock.get_mongodb_connection_string", return_value=""
            ),
            patch.object(
                DistributedLockManager,
                "get_leader_info",
                return_value={"leader": None, "is_leader": False},
            ),
        ):
            result = await lock.health_check()

        _assert_no_secrets(result)
        assert "hunter2" not in str(result)
