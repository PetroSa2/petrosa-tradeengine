"""
Unit tests for the distributed lock manager's lazy-reconnect behaviour.

Covers AC1 (lazy reconnect on acquire/release), AC2 (health visibility and
alert/metric surfacing), and AC3 (startup resilience) of issue #442:
https://github.com/PetroSa2/petrosa-tradeengine/issues/442
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shared import distributed_lock as dl_module
from shared.distributed_lock import (
    LOCK_RECONNECT_MAX_BACKOFF_SECONDS,
    LOCK_RECONNECT_MIN_BACKOFF_SECONDS,
    DistributedLockManager,
)


def _make_fake_mongo_db():
    """Return a (client, db) pair that mimics motor's AsyncIOMotorClient enough
    for the lock manager's init path: ``client.admin.command("ping")`` succeeds
    and ``db.distributed_locks.create_index(...)`` is awaitable."""

    distributed_locks = AsyncMock()
    distributed_locks.create_index = AsyncMock(return_value="lock_name_1")
    distributed_locks.find_one_and_update = AsyncMock(return_value={"_id": "ok"})
    distributed_locks.delete_one = AsyncMock(return_value=AsyncMock(deleted_count=1))

    db_mock = AsyncMock()
    db_mock.distributed_locks = distributed_locks

    admin = AsyncMock()
    admin.command = AsyncMock(return_value={"ok": 1.0})

    client_mock = AsyncMock()
    client_mock.admin = admin
    # AsyncIOMotorClient supports ``client[db_name]`` indexing → return the
    # db mock without going through __getitem__.return_value (AsyncMock would
    # otherwise wrap it).
    client_mock.__getitem__ = lambda self, _name: db_mock
    return client_mock, db_mock


@pytest.fixture
def mgr() -> DistributedLockManager:
    """Fresh DistributedLockManager — never shares state across tests."""
    return DistributedLockManager()


@pytest.mark.asyncio
async def test_acquire_lock_returns_false_when_mongo_unavailable(mgr):
    """AC1: when MongoDB is unavailable, acquire_lock returns False after a
    bounded reconnect attempt — it does not raise."""

    async def always_fail():
        raise ConnectionError("mongo down")

    with patch.object(
        mgr, "_initialize_mongodb", side_effect=always_fail, autospec=False
    ):
        result = await mgr.acquire_lock("signal_test")

    assert result is False
    assert mgr.mongodb_db is None
    # The lazy-reconnect attempt registered itself (cooldown bookkeeping kicked
    # in) and bumped the backoff above the floor.
    assert mgr._last_init_attempt_at is not None
    assert mgr._init_backoff_seconds > LOCK_RECONNECT_MIN_BACKOFF_SECONDS


@pytest.mark.asyncio
async def test_acquire_lock_succeeds_after_mongo_returns(mgr):
    """AC1: client starts None, becomes available — the second acquire_lock
    succeeds without the caller re-instantiating the manager."""

    attempts = {"n": 0}
    fake_client, _fake_db = _make_fake_mongo_db()

    async def fail_then_succeed():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient atlas blip")
        # Second attempt succeeds: mirror the real ``_initialize_mongodb`` body
        # by populating both client + db on ``mgr``.
        mgr.mongodb_client = fake_client
        mgr.mongodb_db = fake_client["petrosa"]
        mgr._init_backoff_seconds = LOCK_RECONNECT_MIN_BACKOFF_SECONDS
        mgr._init_failure_count = 0
        mgr._last_init_error = None
        mgr._alert_emitted_for_current_outage = False

    with patch.object(
        mgr, "_initialize_mongodb", side_effect=fail_then_succeed, autospec=False
    ):
        first = await mgr.acquire_lock("signal_test")
        assert first is False

        # Bypass the backoff cooldown by clearing the throttle timestamp so the
        # second call attempts a reconnect immediately.
        mgr._last_init_attempt_at = None

        second = await mgr.acquire_lock("signal_test")

    assert second is True
    # The instance is the same (no re-instantiation of the manager).
    assert mgr.mongodb_db is not None
    assert mgr._init_failure_count == 0


@pytest.mark.asyncio
async def test_reconnect_respects_backoff_cooldown(mgr):
    """AC1: while inside the backoff window, no additional ``_initialize_mongodb``
    call is made — protects Atlas from being hammered during an outage."""

    call_count = {"n": 0}

    async def fail_init():
        call_count["n"] += 1
        raise ConnectionError("still down")

    with patch.object(
        mgr, "_initialize_mongodb", side_effect=fail_init, autospec=False
    ):
        # First attempt: counts as one reconnect call.
        first = await mgr.acquire_lock("signal_test")
        assert first is False
        assert call_count["n"] == 1

        # Immediately retry — should hit the cooldown short-circuit
        # (no new ``_initialize_mongodb`` call).
        second = await mgr.acquire_lock("signal_test")
        assert second is False
        assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_reconnect_backoff_grows_exponentially_with_cap(mgr):
    """AC1: repeated failures grow the backoff (1 → 2 → 4 → … → cap)."""

    async def fail_init():
        raise ConnectionError("still down")

    with patch.object(
        mgr, "_initialize_mongodb", side_effect=fail_init, autospec=False
    ):
        previous = LOCK_RECONNECT_MIN_BACKOFF_SECONDS
        for _ in range(8):
            # Force the cooldown to elapse so each call triggers a real attempt.
            mgr._last_init_attempt_at = None
            await mgr.acquire_lock("signal_test")
            assert mgr._init_backoff_seconds >= previous
            previous = mgr._init_backoff_seconds

        assert mgr._init_backoff_seconds == LOCK_RECONNECT_MAX_BACKOFF_SECONDS


@pytest.mark.asyncio
async def test_release_lock_lazy_reconnects(mgr):
    """AC1: release_lock follows the same lazy-reconnect path as acquire_lock —
    a transient Mongo blip must not strand held locks."""

    attempts = {"n": 0}
    fake_client, _fake_db = _make_fake_mongo_db()

    async def fail_then_succeed():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("blip")
        mgr.mongodb_client = fake_client
        mgr.mongodb_db = fake_client["petrosa"]
        mgr._init_backoff_seconds = LOCK_RECONNECT_MIN_BACKOFF_SECONDS
        mgr._init_failure_count = 0

    with patch.object(
        mgr, "_initialize_mongodb", side_effect=fail_then_succeed, autospec=False
    ):
        # First release attempt fails — Mongo is down.
        first = await mgr.release_lock("signal_test")
        assert first is False

        # Cooldown bypass + retry succeeds — the lock can be released without
        # a pod restart.
        mgr._last_init_attempt_at = None
        second = await mgr.release_lock("signal_test")

    assert second is True


@pytest.mark.asyncio
async def test_health_check_reports_unhealthy_when_disconnected(mgr):
    """AC2: health_check reports lock-manager Mongo connectivity as a
    first-class unhealthy signal."""

    health = await mgr.health_check()

    assert health["status"] == "unhealthy"
    assert health["mongodb_connected"] is False
    assert "reconnect" in health
    assert "init_failure_count" in health["reconnect"]


@pytest.mark.asyncio
async def test_health_check_reports_healthy_when_connected(mgr):
    """AC2 (inverse): a connected lock manager reports healthy."""

    fake_client, fake_db = _make_fake_mongo_db()
    mgr.mongodb_client = fake_client
    mgr.mongodb_db = fake_db

    leader_election = AsyncMock()
    leader_election.find_one = AsyncMock(return_value=None)
    fake_db.leader_election = leader_election

    health = await mgr.health_check()

    assert health["status"] == "healthy"
    assert health["mongodb_connected"] is True


@pytest.mark.asyncio
async def test_lock_init_failed_counter_increments_on_failure(mgr):
    """AC2: lock_init_failed_total fires on every ``_initialize_mongodb``
    failure so operators can alert on a silent trading-halt. Exercises the
    real ``_initialize_mongodb`` body by making the motor client raise."""

    before = dl_module.lock_init_failed_total._value.get()

    class _BoomClient:
        def __init__(self, *_args, **_kwargs):
            raise ConnectionError("atlas write-block (synthetic)")

    with patch("motor.motor_asyncio.AsyncIOMotorClient", side_effect=_BoomClient):
        await mgr.acquire_lock("signal_test")

    after = dl_module.lock_init_failed_total._value.get()
    assert after >= before + 1, (
        f"lock_init_failed_total should have ticked at least once (was {before}, "
        f"now {after})"
    )
    # And the manager's own bookkeeping recorded the failure.
    assert mgr._init_failure_count >= 1
    assert mgr._last_init_error is not None


@pytest.mark.asyncio
async def test_initialize_does_not_raise_when_mongo_unavailable(mgr):
    """AC3: a failed boot must not leave the engine in a permanently-degraded-
    but-"healthy" state. ``initialize()`` swallows the init exception and the
    manager is then ready to lazy-reconnect (verified above)."""

    async def always_fail():
        raise ConnectionError("atlas write-block")

    with (
        patch.object(
            mgr, "_initialize_mongodb", side_effect=always_fail, autospec=False
        ),
        patch.object(mgr, "_try_become_leader", new=AsyncMock(return_value=False)),
        patch.object(mgr, "_cleanup_expired_locks", new=AsyncMock(return_value=None)),
    ):
        # Should not raise even though Mongo is unreachable.
        await mgr.initialize()

    # Cancel the cleanup task spawned by initialize() so the test doesn't leak it.
    if mgr.lock_cleanup_task is not None:
        mgr.lock_cleanup_task.cancel()
        try:
            await mgr.lock_cleanup_task
        except BaseException:
            # CancelledError inherits from BaseException in 3.8+; swallow it.
            pass

    # Health correctly reflects the degraded state.
    health = await mgr.health_check()
    assert health["status"] == "unhealthy"
    assert health["mongodb_connected"] is False
