"""
Integration tests for distributed lock coordination in Dispatcher.

These tests verify that distributed locking prevents race conditions when multiple
pods process the same signal concurrently. This is critical for production safety
in a Kubernetes deployment with multiple replicas.

Related:
    - Issue: https://github.com/PetroSa2/petrosa-tradeengine/issues/177
    - Parent Issue: https://github.com/PetroSa2/petrosa-tradeengine/issues/173
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from contracts.signal import Signal, StrategyMode
from tests.integration.fakes import FakeExchange, FakePositionManager


@pytest.fixture
def fake_exchange():
    """Create a fake exchange for testing."""
    return FakeExchange()


@pytest.fixture
def fake_position_manager():
    """Create a fake position manager for testing."""
    return FakePositionManager()


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing."""
    return Signal(
        strategy_id="test-lock-strategy",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
        strategy_mode=StrategyMode.DETERMINISTIC,
    )


@pytest.fixture
def mock_lock_manager_init():
    """Fixture to mock distributed_lock_manager.initialize across all tests."""
    from shared.distributed_lock import distributed_lock_manager

    with patch.object(distributed_lock_manager, "initialize", new_callable=AsyncMock):
        yield


def create_signal_with_fingerprint(
    sample_signal: Signal, signal_id: str, timestamp: datetime
) -> Signal:
    """Helper function to create a signal with a specific fingerprint.

    Args:
        sample_signal: Base signal to copy fields from
        signal_id: Signal ID to use (determines fingerprint for lock key)
        timestamp: Timestamp for the signal (affects duplicate cache)

    Returns:
        Signal with specified signal_id and timestamp
    """
    return Signal(
        strategy_id=sample_signal.strategy_id,
        symbol=sample_signal.symbol,
        signal_type=sample_signal.signal_type,
        action=sample_signal.action,
        confidence=sample_signal.confidence,
        strength=sample_signal.strength,
        timeframe=sample_signal.timeframe,
        price=sample_signal.price,
        quantity=sample_signal.quantity,
        current_price=sample_signal.current_price,
        timestamp=timestamp,
        source=sample_signal.source,
        strategy=sample_signal.strategy,
        strategy_mode=sample_signal.strategy_mode,
        signal_id=signal_id,  # Same signal_id = same fingerprint = same lock key
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lock_prevents_concurrent_processing(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that lock prevents same signal from being processed concurrently.

    When the same signal is dispatched twice concurrently, only one order
    should be executed because the lock prevents the second pod from processing.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    # Track lock acquisition attempts
    lock_attempts = []
    lock_releases = []

    async def mock_execute_with_lock(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to track calls and simulate lock behavior."""
        lock_attempts.append(lock_key)

        # First call succeeds, subsequent calls fail (simulating another pod holding lock)
        if len(lock_attempts) == 1:
            # First pod acquires lock successfully
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                lock_releases.append(lock_key)
        else:
            # Second pod fails to acquire lock
            raise Exception(f"Failed to acquire lock '{lock_key}'")

    # Mock the distributed lock manager
    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Mock _generate_signal_id to return different IDs for duplicate cache bypass
        # This allows both signals to pass duplicate check while using same fingerprint for lock
        signal_id_counter = 0

        def mock_generate_signal_id(signal):
            """Mock to generate unique signal IDs to bypass duplicate cache."""
            nonlocal signal_id_counter
            signal_id_counter += 1
            # Return different IDs so duplicate cache doesn't catch them
            return f"test-signal-id-{signal_id_counter}"

        # Create two signals with same fingerprint (same signal_id) but will have different
        # duplicate cache IDs due to mocking
        import time

        base_timestamp = datetime.utcnow()
        shared_signal_id = f"test-lock-signal-{int(time.time())}"

        signal1 = create_signal_with_fingerprint(
            sample_signal, shared_signal_id, base_timestamp
        )
        signal2 = create_signal_with_fingerprint(
            sample_signal, shared_signal_id, base_timestamp
        )

        # Patch _generate_signal_id to bypass duplicate cache
        with patch.object(
            dispatcher, "_generate_signal_id", side_effect=mock_generate_signal_id
        ):
            # Process signals concurrently (simulating two pods)
            # Both signals will have the same fingerprint, so they'll use the same lock key
            results = await asyncio.gather(
                dispatcher.dispatch(signal1),
                dispatcher.dispatch(signal2),
                return_exceptions=True,
            )

            # Verify: Only ONE order should be executed
            assert len(fake_exchange.executed_orders) == 1, (
                f"Expected 1 order, but got {len(fake_exchange.executed_orders)}. "
                f"This indicates the lock did not prevent concurrent processing."
            )

            # Verify: One result should be "executed", one should be "skipped_duplicate"
            executed_count = sum(
                1
                for r in results
                if isinstance(r, dict) and r.get("status") == "executed"
            )
            skipped_count = sum(
                1
                for r in results
                if isinstance(r, dict) and r.get("status") == "skipped_duplicate"
            )

            assert (
                executed_count == 1
            ), f"Expected 1 executed result, got {executed_count}. Results: {results}"
            assert (
                skipped_count == 1
            ), f"Expected 1 skipped result, got {skipped_count}. Results: {results}"

            # Verify: Lock was attempted twice
            assert (
                len(lock_attempts) == 2
            ), f"Expected 2 lock attempts, got {len(lock_attempts)}"

            # Verify: Lock was released after first execution
            assert (
                len(lock_releases) == 1
            ), f"Expected 1 lock release, got {len(lock_releases)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lock_acquisition_failure_skips_gracefully(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that failed lock acquisition skips processing without error.

    When lock acquisition fails, the dispatcher should gracefully skip
    processing and return a skipped_duplicate status without raising an error.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    # Mock lock manager to always fail lock acquisition
    async def mock_execute_with_lock_fail(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to always fail."""
        raise Exception(f"Failed to acquire lock '{lock_key}'")

    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock_fail,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Process signal (should skip gracefully)
        result = await dispatcher.dispatch(sample_signal)

        # Verify: Processing was skipped
        assert (
            result["status"] == "skipped_duplicate"
        ), f"Expected 'skipped_duplicate' status, got '{result.get('status')}'"
        assert (
            "lock" in result.get("reason", "").lower()
            or "pod" in result.get("reason", "").lower()
        ), f"Reason should mention lock or pod: {result.get('reason')}"

        # Verify: No order was executed
        assert len(fake_exchange.executed_orders) == 0, (
            f"Expected 0 orders, but got {len(fake_exchange.executed_orders)}. "
            f"Lock failure should prevent order execution."
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lock_released_after_success(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that lock is released after successful processing.

    The lock should be released in a finally block even if processing succeeds.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    lock_released = False

    async def mock_execute_with_lock_success(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to succeed and track release."""
        nonlocal lock_released
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            lock_released = True  # Simulate lock release in finally block

    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock_success,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Process signal
        await dispatcher.dispatch(sample_signal)

        # Verify: Function was executed (order was placed)
        assert (
            len(fake_exchange.executed_orders) == 1
        ), f"Expected 1 order, got {len(fake_exchange.executed_orders)}"

        # Verify: Lock was released
        assert lock_released, "Lock should be released after successful processing"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lock_released_after_failure(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that lock is released even if processing fails.

    The lock should be released in a finally block even if an error occurs
    during order execution.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    lock_released = False

    async def mock_execute_with_lock_error(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to fail during execution but still release lock."""
        nonlocal lock_released
        try:
            # Actually call the function first, then simulate error
            await func(*args, **kwargs)
            # Simulate error after execution (e.g., during result processing)
            raise Exception("Simulated error after order execution")
        finally:
            lock_released = True  # Lock should still be released

    # Make exchange throw error during execution
    async def failing_execute(order):
        raise Exception("Simulated exchange error")

    fake_exchange.execute = failing_execute

    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock_error,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Process signal (should handle error gracefully)
        result = await dispatcher.dispatch(sample_signal)

        # Verify: Error was caught (status should be error or similar)
        # The dispatcher may return error status or re-raise, but lock should be released
        assert (
            result.get("status") in ["error", "failed"]
            or "error" in str(result).lower()
        ), f"Expected error status, got: {result}"

        # Verify: Lock was released even after error
        assert lock_released, "Lock should be released even after processing failure"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lock_key_includes_signal_fingerprint(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that lock key includes signal fingerprint to prevent collision.

    The lock key should be unique per signal to ensure different signals
    don't block each other.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    captured_lock_key = None

    async def mock_execute_with_lock_capture(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to capture lock key."""
        nonlocal captured_lock_key
        captured_lock_key = lock_key
        return await func(*args, **kwargs)

    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock_capture,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Process signal
        await dispatcher.dispatch(sample_signal)

        # Verify: lock_key includes signal components
        assert captured_lock_key is not None, "Lock key should be captured"
        assert (
            "signal_" in captured_lock_key
        ), f"Lock key should start with 'signal_', got: {captured_lock_key}"

        # Verify: Lock key includes signal identifier (strategy_id or symbol)
        # The fingerprint is generated from signal fields, so it should include
        # strategy_id, symbol, or other identifying fields
        assert (
            sample_signal.strategy_id in captured_lock_key
            or sample_signal.symbol in captured_lock_key
        ), (
            f"Lock key should include signal identifier. "
            f"Key: {captured_lock_key}, Strategy: {sample_signal.strategy_id}, "
            f"Symbol: {sample_signal.symbol}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_pod_waits_for_first_pod_lock_release(
    fake_exchange: FakeExchange,
    fake_position_manager: FakePositionManager,
    sample_signal: Signal,
    mock_lock_manager_init,
):
    """Test that second pod waits for first pod's lock to release.

    This test simulates a scenario where:
    1. First pod acquires lock and processes signal
    2. Second pod tries to acquire lock (fails because first pod holds it)
    3. First pod releases lock
    4. Second pod can now acquire lock (if we retry, but in practice it skips)

    In the actual implementation, the second pod will skip processing
    if lock acquisition fails, which is the expected behavior.
    """
    from shared.distributed_lock import distributed_lock_manager
    from tradeengine.dispatcher import Dispatcher

    lock_held_by_first = False
    lock_released_by_first = False

    async def mock_execute_with_lock_sequential(lock_key, func, *args, **kwargs):
        """Mock execute_with_lock to simulate sequential lock acquisition."""
        nonlocal lock_held_by_first, lock_released_by_first

        if not lock_held_by_first:
            # First pod acquires lock
            lock_held_by_first = True
            try:
                # Simulate some processing time
                await asyncio.sleep(0.1)
                result = await func(*args, **kwargs)
                return result
            finally:
                # First pod releases lock
                lock_held_by_first = False
                lock_released_by_first = True
        else:
            # Second pod fails to acquire lock (first pod still holds it)
            raise Exception(f"Failed to acquire lock '{lock_key}'")

    with patch.object(
        distributed_lock_manager,
        "execute_with_lock",
        side_effect=mock_execute_with_lock_sequential,
    ):
        dispatcher = Dispatcher(exchange=fake_exchange)
        dispatcher.position_manager = fake_position_manager

        # Mock _generate_signal_id to return different IDs for duplicate cache bypass
        signal_id_counter = 0

        def mock_generate_signal_id(signal):
            """Mock to generate unique signal IDs to bypass duplicate cache."""
            nonlocal signal_id_counter
            signal_id_counter += 1
            return f"test-signal-id-{signal_id_counter}"

        # Create two signals with same fingerprint (same signal_id) but will have different
        # duplicate cache IDs due to mocking
        import time

        base_timestamp = datetime.utcnow()
        shared_signal_id = f"test-sequential-lock-signal-{int(time.time())}"

        signal1 = create_signal_with_fingerprint(
            sample_signal, shared_signal_id, base_timestamp
        )
        signal2 = create_signal_with_fingerprint(
            sample_signal, shared_signal_id, base_timestamp
        )

        # Patch _generate_signal_id to bypass duplicate cache
        with patch.object(
            dispatcher, "_generate_signal_id", side_effect=mock_generate_signal_id
        ):
            # Process signals (simulating two pods)
            # First call should succeed, second should skip due to lock
            results = await asyncio.gather(
                dispatcher.dispatch(signal1),
                dispatcher.dispatch(signal2),
                return_exceptions=True,
            )

            # Verify: Only first pod executed order
            assert (
                len(fake_exchange.executed_orders) == 1
            ), f"Expected 1 order (from first pod), got {len(fake_exchange.executed_orders)}"

            # Verify: First result is executed, second is skipped
            first_result = results[0]
            second_result = results[1]

            assert (
                isinstance(first_result, dict)
                and first_result.get("status") == "executed"
            ), f"First pod should execute: {first_result}"
            assert (
                isinstance(second_result, dict)
                and second_result.get("status") == "skipped_duplicate"
            ), f"Second pod should skip: {second_result}"

            # Verify: Lock was released by first pod
            assert (
                lock_released_by_first
            ), "First pod should release lock after processing"
