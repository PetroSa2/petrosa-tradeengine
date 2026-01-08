"""
Global test configuration and fixtures for petrosa-tradeengine.
"""

import os
from typing import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Set up test environment BEFORE any imports that might trigger validation
os.environ.update(
    {
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DATABASE": "test_tradeengine",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "DEBUG",
        "NATS_URL": "nats://localhost:4222",
        "BINANCE_API_KEY": "test_api_key",
        "BINANCE_API_SECRET": "test_api_secret",
        "BINANCE_TESTNET": "true",
        "REDIS_URL": "redis://localhost:6379",
        "JWT_SECRET_KEY": "test_jwt_secret_key_for_testing_only",
        "PROMETHEUS_ENABLED": "false",
        "NATS_ENABLED": "false",
        "SIMULATION_ENABLED": "true",
    }
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> Generator[None, None, None]:
    """Set up test environment with mocked dependencies."""
    # Environment is already set up above
    yield


def _restore_otel_init_function(module):
    """Helper to restore original configure_logging function from module."""
    import importlib
    import sys
    from unittest.mock import MagicMock, patch

    if not hasattr(module, "configure_logging"):
        return

    # Stop all patches first
    patch.stopall()

    func = getattr(module, "configure_logging", None)
    if func is None:
        return

    # Check if it's a mock (has mock attributes)
    is_mock = (
        hasattr(func, "return_value")
        or hasattr(func, "side_effect")
        or isinstance(func, MagicMock)
        or hasattr(func, "_mock_")
        or str(type(func).__name__)
        in ("MagicMock", "Mock", "AsyncMock", "NonCallableMagicMock")
    )

    # If it's wrapped (from @patch or similar), try to unwrap
    if hasattr(func, "__wrapped__"):
        module.configure_logging = func.__wrapped__
        # Check if unwrapped is still a mock
        if hasattr(module.configure_logging, "return_value") or hasattr(
            module.configure_logging, "side_effect"
        ):
            is_mock = True
        else:
            return  # Successfully restored

    if is_mock:
        # It's a mock, try to restore from reload
        try:
            # Reload the module to get fresh unpatched function
            module_file = getattr(module, "__file__", None)
            if module_file and module_file.endswith(".py"):
                # Only reload if it's a Python file (not a compiled module)
                importlib.reload(module)
                # Verify it's now restored
                new_func = getattr(module, "configure_logging", None)
                if new_func and not (
                    hasattr(new_func, "return_value")
                    or hasattr(new_func, "side_effect")
                ):
                    return  # Successfully restored
        except (AttributeError, TypeError, ImportError, Exception):
            # If reload fails, try to manually restore from source
            pass


def _restore_all_otel_init_patches():
    """Aggressively restore all otel_init.configure_logging patches across all modules."""
    import sys
    from unittest.mock import patch

    # Stop all patches first
    patch.stopall()

    # Restore configure_logging in otel_init module
    if "otel_init" in sys.modules:
        _restore_otel_init_function(sys.modules["otel_init"])

    # Also check tradeengine.api.otel_init if it exists
    if "tradeengine.api" in sys.modules:
        api_module = sys.modules["tradeengine.api"]
        if hasattr(api_module, "otel_init"):
            otel_init_attr = getattr(api_module, "otel_init", None)
            if otel_init_attr and hasattr(otel_init_attr, "configure_logging"):
                # Check if it's patched
                func = getattr(otel_init_attr, "configure_logging", None)
                if func and (
                    hasattr(func, "return_value") or hasattr(func, "side_effect")
                ):
                    # It's patched, restore from original otel_init module
                    if "otel_init" in sys.modules:
                        original_func = getattr(
                            sys.modules["otel_init"], "configure_logging", None
                        )
                        if original_func and not (
                            hasattr(original_func, "return_value")
                            or hasattr(original_func, "side_effect")
                        ):
                            otel_init_attr.configure_logging = original_func

    # Check all modules that might have otel_init imported
    for module_name in list(sys.modules.keys()):
        if "tradeengine" in module_name or "api" in module_name:
            try:
                module = sys.modules[module_name]
                if hasattr(module, "otel_init"):
                    otel_init_attr = getattr(module, "otel_init", None)
                    if otel_init_attr and hasattr(otel_init_attr, "configure_logging"):
                        func = getattr(otel_init_attr, "configure_logging", None)
                        if func and (
                            hasattr(func, "return_value")
                            or hasattr(func, "side_effect")
                        ):
                            # It's patched, restore from original
                            if "otel_init" in sys.modules:
                                original_func = getattr(
                                    sys.modules["otel_init"], "configure_logging", None
                                )
                                if original_func and not (
                                    hasattr(original_func, "return_value")
                                    or hasattr(original_func, "side_effect")
                                ):
                                    otel_init_attr.configure_logging = original_func
            except (AttributeError, KeyError, Exception):
                pass


@pytest.fixture(autouse=True)
def cleanup_logging_state():
    """Clean up logging state before and after each test to prevent test isolation issues."""
    import importlib
    import logging
    import sys
    from unittest.mock import MagicMock, patch

    # Aggressively stop all patches BEFORE test (ensure clean state)
    # Stop all active patches multiple times to catch all of them
    for _ in range(3):
        patch.stopall()

    # BEFORE test: Restore configure_logging if it's mocked
    # First try to unwrap, if that doesn't work and it's still mocked, reload module
    if "otel_init" in sys.modules:
        module = sys.modules["otel_init"]
        func = getattr(module, "configure_logging", None)
        if func:
            # Check if it's mocked
            is_mock = (
                isinstance(func, MagicMock)
                or hasattr(func, "return_value")
                or hasattr(func, "side_effect")
                or hasattr(func, "_mock_")
            )
            # If mocked, try to unwrap first (prefer unwrapping over reloading to preserve state)
            if is_mock:
                if hasattr(func, "__wrapped__"):
                    # Unwrap to get real function
                    unwrapped = func.__wrapped__
                    # Check if unwrapped is also mocked
                    unwrapped_is_mock = (
                        isinstance(unwrapped, MagicMock)
                        or hasattr(unwrapped, "return_value")
                        or hasattr(unwrapped, "side_effect")
                    )
                    if not unwrapped_is_mock:
                        # Successfully unwrapped - restore without reloading (preserves _global_logger_provider)
                        module.configure_logging = unwrapped
                        # Also update api_module reference
                        if "tradeengine.api" in sys.modules:
                            api_module = sys.modules["tradeengine.api"]
                            if (
                                hasattr(api_module, "otel_init")
                                and api_module.otel_init is module
                            ):
                                if hasattr(api_module.otel_init, "configure_logging"):
                                    api_module.otel_init.configure_logging = unwrapped
                    else:
                        # Unwrapped is also mocked, need to reload (last resort)
                        module_file = getattr(module, "__file__", None)
                        if module_file and module_file.endswith(".py"):
                            try:
                                # Reload to get fresh unpatched module
                                # NOTE: We DON'T preserve _global_logger_provider here - it should be None for clean state
                                importlib.reload(module)
                                # Stop patches again after reload
                                for _ in range(3):
                                    patch.stopall()
                            except (AttributeError, TypeError, ImportError, Exception):
                                pass
                else:
                    # No __wrapped__, need to reload (last resort)
                    module_file = getattr(module, "__file__", None)
                    if module_file and module_file.endswith(".py"):
                        try:
                            # Reload to get fresh unpatched module
                            # NOTE: We DON'T preserve _global_logger_provider here - it should be None for clean state
                            importlib.reload(module)
                            # Stop patches again after reload
                            for _ in range(3):
                                patch.stopall()
                        except (AttributeError, TypeError, ImportError, Exception):
                            pass

    # Cleanup BEFORE test (ensure clean state)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)

    # Also clear uvicorn loggers
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)
        logger.propagate = True

    # Reset otel_init global state after reload
    try:
        if "otel_init" in sys.modules:
            module = sys.modules["otel_init"]
            if hasattr(module, "_global_logger_provider"):
                module._global_logger_provider = None
    except (ImportError, AttributeError, KeyError, Exception):
        pass

    yield

    # Aggressively stop all patches AFTER test (ensure next test starts clean)
    # Stop all active patches multiple times to catch all of them
    for _ in range(3):
        patch.stopall()

    # Cleanup AFTER test (ensure next test starts clean)
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)

    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)
        logger.propagate = True

    # Restore otel_init module and function AFTER test to ensure patches are completely cleared
    try:
        # Aggressively restore all patches across all modules
        _restore_all_otel_init_patches()

        # Additional aggressive cleanup: directly check and restore function in all modules
        for module_name in list(sys.modules.keys()):
            if "otel_init" in module_name or "api" in module_name:
                try:
                    module = sys.modules[module_name]
                    if hasattr(module, "otel_init"):
                        otel_attr = getattr(module, "otel_init", None)
                        if otel_attr and hasattr(otel_attr, "configure_logging"):
                            func = getattr(otel_attr, "configure_logging", None)
                            if func and (
                                isinstance(func, MagicMock)
                                or hasattr(func, "return_value")
                                or hasattr(func, "side_effect")
                            ):
                                # It's still a mock, restore from original otel_init
                                if "otel_init" in sys.modules:
                                    original = getattr(
                                        sys.modules["otel_init"],
                                        "configure_logging",
                                        None,
                                    )
                                    if original and not (
                                        isinstance(original, MagicMock)
                                        or hasattr(original, "return_value")
                                    ):
                                        otel_attr.configure_logging = original
                except (AttributeError, KeyError, Exception):
                    pass

        # AFTER test: ALWAYS reload otel_init module to ensure fresh unpatched state for next test
        # This is critical because patches can persist even after patch.stopall()
        # BUT: We don't need to preserve _global_logger_provider here because the cleanup
        # fixture should reset it to None anyway for the next test
        fresh_otel_init = None
        if "otel_init" in sys.modules:
            module = sys.modules["otel_init"]
            module_file = getattr(module, "__file__", None)
            if module_file and module_file.endswith(".py"):
                try:
                    # Reload to get fresh unpatched module
                    importlib.reload(module)
                    fresh_otel_init = sys.modules["otel_init"]
                    # Stop patches again after reload (in case reload triggered new patches)
                    for _ in range(3):
                        patch.stopall()
                    # Reset _global_logger_provider after reload (cleanup for next test)
                    if hasattr(fresh_otel_init, "_global_logger_provider"):
                        fresh_otel_init._global_logger_provider = None
                except (AttributeError, TypeError, ImportError, Exception):
                    fresh_otel_init = sys.modules.get("otel_init")

        # CRITICAL: Update all references to otel_init in other modules after reload
        # This ensures modules like tradeengine.api.otel_init point to the fresh module
        # and not a mock or stale reference
        if fresh_otel_init is None:
            fresh_otel_init = sys.modules.get("otel_init")

        if fresh_otel_init:
            # First, explicitly restore tradeengine.api.otel_init if it exists
            # This is critical because test_api_lifespan_integration patches this
            if "tradeengine.api" in sys.modules:
                api_module = sys.modules["tradeengine.api"]
                if hasattr(api_module, "otel_init"):
                    # Force restore to fresh module
                    api_module.otel_init = fresh_otel_init
                    # Also ensure configure_logging is restored
                    if hasattr(fresh_otel_init, "configure_logging"):
                        fresh_func = fresh_otel_init.configure_logging
                        # Ensure it's not a mock
                        if not (
                            isinstance(fresh_func, MagicMock)
                            or hasattr(fresh_func, "return_value")
                        ):
                            # Ensure api_module.otel_init.configure_logging points to real function
                            if hasattr(api_module.otel_init, "configure_logging"):
                                # Double-check it's not mocked
                                api_func = api_module.otel_init.configure_logging
                                if isinstance(api_func, MagicMock) or hasattr(
                                    api_func, "return_value"
                                ):
                                    # Still mocked, restore from fresh module
                                    api_module.otel_init.configure_logging = fresh_func

            # Update references in ALL other modules that import otel_init
            for module_name in list(sys.modules.keys()):
                try:
                    mod = sys.modules[module_name]
                    # Check if module has otel_init as an attribute
                    if hasattr(mod, "otel_init"):
                        # Always update to fresh module to ensure consistency
                        mod.otel_init = fresh_otel_init
                except (AttributeError, KeyError, Exception):
                    pass

        # Reset global state
        if "otel_init" in sys.modules:
            module = sys.modules["otel_init"]
            if hasattr(module, "_global_logger_provider"):
                module._global_logger_provider = None
    except (ImportError, AttributeError, KeyError, Exception):
        # Best-effort recovery: if resetting global state fails,
        # we intentionally ignore the error. At this point in the cleanup
        # path during test setup, failing hard would make tests more
        # fragile without improving correctness.
        pass


def get_real_configure_logging():
    """
    Helper function to get the real configure_logging function, not a mock.
    Use this in tests that need to call the actual function when patches might be active.

    ALWAYS reloads the module to ensure we get a fresh unpatched function.
    This is critical because patches can persist even after patch.stopall(),
    and detecting if a function is mocked is unreliable.

    Returns a wrapper function that has a `_module` attribute pointing to the fresh module.
    IMPORTANT: After calling this function, set _global_logger_provider on the returned
    function's `_module` attribute (e.g., `configure_logging._module._global_logger_provider = None`).
    This ensures the function reads from the correct module after reload.
    """
    import importlib
    import sys
    from unittest.mock import MagicMock, patch

    # Stop any active patches aggressively
    for _ in range(3):
        patch.stopall()

    # ALWAYS reload the module to ensure fresh unpatched state
    # This is the most reliable way to ensure patches from earlier tests don't persist
    # NOTE: This will reset _global_logger_provider to None, but tests should set it AFTER calling this function
    if "otel_init" in sys.modules:
        module = sys.modules["otel_init"]
        module_file = getattr(module, "__file__", None)

        if module_file and module_file.endswith(".py"):
            try:
                # Reload to get fresh unpatched module
                importlib.reload(module)
                # Stop patches again after reload (in case reload triggered new patches)
                for _ in range(3):
                    patch.stopall()

                    # Update all references to otel_init in other modules after reload
                    fresh_otel_init = sys.modules["otel_init"]
                    fresh_func = getattr(fresh_otel_init, "configure_logging", None)

                    if fresh_func:
                        # CRITICAL: Update ALL references to otel_init BEFORE checking if function is valid
                        # This ensures that api_module.otel_init points to the fresh module
                        # BEFORE we try to get the function, so there's no stale reference

                        # First, update tradeengine.api.otel_init
                        if "tradeengine.api" in sys.modules:
                            api_module = sys.modules["tradeengine.api"]
                            if hasattr(api_module, "otel_init"):
                                # CRITICAL: Update the reference FIRST
                                api_module.otel_init = fresh_otel_init

                        # Update references in all other modules that import otel_init
                        # CRITICAL: This includes test modules that import otel_init at module level
                        for module_name in list(sys.modules.keys()):
                            try:
                                mod = sys.modules[module_name]
                                # Update if module has otel_init as an attribute
                                if hasattr(mod, "otel_init"):
                                    # Check if it's the old module reference
                                    old_ref = getattr(mod, "otel_init", None)
                                    if old_ref is not fresh_otel_init:
                                        mod.otel_init = fresh_otel_init
                                # Also check if the module itself is 'otel_init' (for direct imports)
                                if (
                                    module_name == "otel_init"
                                    and mod is not fresh_otel_init
                                ):
                                    sys.modules[module_name] = fresh_otel_init
                            except (AttributeError, KeyError, Exception):
                                pass

                        # CRITICAL: Also update globals in calling frame if possible
                        # This ensures that when tests do `import otel_init`, they get the fresh module
                        try:
                            import inspect

                            frame = inspect.currentframe()
                            # Go up the stack to find the test function that called this
                            for _ in range(5):  # Check up to 5 frames up
                                if frame is None:
                                    break
                                if "otel_init" in frame.f_globals:
                                    frame.f_globals["otel_init"] = fresh_otel_init
                                frame = frame.f_back
                        except Exception:
                            pass  # If we can't update globals, that's okay

                        # Now get the function again from the fresh module (not from api_module.otel_init)
                        # This ensures we get the actual function from the reloaded module
                        fresh_func = getattr(fresh_otel_init, "configure_logging", None)

                        if fresh_func:
                            # Verify it's not a mock after reload and reference updates
                            reloaded_is_mock = (
                                isinstance(fresh_func, MagicMock)
                                or hasattr(fresh_func, "return_value")
                                or hasattr(fresh_func, "side_effect")
                                or hasattr(fresh_func, "_mock_")
                                or str(type(fresh_func).__name__)
                                in (
                                    "MagicMock",
                                    "Mock",
                                    "AsyncMock",
                                    "NonCallableMagicMock",
                                )
                            )

                            # If still mocked after reload, force reload again
                            if reloaded_is_mock:
                                try:
                                    importlib.reload(fresh_otel_init)
                                    for _ in range(3):
                                        patch.stopall()
                                    # Update references again
                                    fresh_otel_init = sys.modules["otel_init"]
                                    if "tradeengine.api" in sys.modules:
                                        sys.modules["tradeengine.api"].otel_init = (
                                            fresh_otel_init
                                        )
                                    fresh_func = getattr(
                                        fresh_otel_init, "configure_logging", None
                                    )
                                    reloaded_is_mock = (
                                        (
                                            isinstance(fresh_func, MagicMock)
                                            or hasattr(fresh_func, "return_value")
                                            or hasattr(fresh_func, "side_effect")
                                            or hasattr(fresh_func, "_mock_")
                                        )
                                        if fresh_func
                                        else True
                                    )
                                except Exception:
                                    pass

                            if fresh_func and not reloaded_is_mock:
                                # Double-check that api_module.otel_init.configure_logging is also real
                                if "tradeengine.api" in sys.modules:
                                    api_module = sys.modules["tradeengine.api"]
                                    if hasattr(api_module, "otel_init"):
                                        api_module.otel_init = fresh_otel_init  # Ensure reference is updated
                                        api_func = getattr(
                                            api_module.otel_init,
                                            "configure_logging",
                                            None,
                                        )
                                        if api_func and (
                                            isinstance(api_func, MagicMock)
                                            or hasattr(api_func, "return_value")
                                        ):
                                            # Still mocked on api_module reference, restore it
                                            api_module.otel_init.configure_logging = (
                                                fresh_func
                                            )
                                            # Get fresh_func again to ensure it's the restored one
                                            fresh_func = getattr(
                                                fresh_otel_init,
                                                "configure_logging",
                                                None,
                                            )

                                # CRITICAL: Return a wrapper that ensures _global_logger_provider is set on the correct module
                                # This ensures that when tests set _global_logger_provider, it's on the same module the function reads from
                                # Use default argument to capture fresh_func in closure (avoids B023 warning)
                                def wrapped_configure_logging(
                                    *args, func=fresh_func, **kwargs
                                ):
                                    # Ensure we're using the function from the fresh module
                                    # The function's __globals__ points to the module where it's defined
                                    # Call the function - it will read _global_logger_provider from its module's globals
                                    return func(*args, **kwargs)

                                # Attach the module reference to the wrapper for tests to use
                                wrapped_configure_logging._module = fresh_otel_init
                                wrapped_configure_logging._original_func = fresh_func
                                return wrapped_configure_logging
                            # If still mocked after reload, try unwrapping
                            if fresh_func and hasattr(fresh_func, "__wrapped__"):
                                unwrapped = fresh_func.__wrapped__
                                if not (
                                    isinstance(unwrapped, MagicMock)
                                    or hasattr(unwrapped, "return_value")
                                    or hasattr(unwrapped, "side_effect")
                                ):
                                    # Return wrapper for unwrapped function too
                                    # Use default argument to capture unwrapped in closure (avoids B023 warning)
                                    def wrapped_configure_logging(
                                        *args, func=unwrapped, **kwargs
                                    ):
                                        return func(*args, **kwargs)

                                    wrapped_configure_logging._module = fresh_otel_init
                                    wrapped_configure_logging._original_func = unwrapped
                                    return wrapped_configure_logging
            except (AttributeError, TypeError, ImportError, Exception):
                # If reload fails, try to get from module as-is
                pass

    # Fallback: try to get from module without reload
    if "otel_init" in sys.modules:
        module = sys.modules["otel_init"]
        func = getattr(module, "configure_logging", None)
        if func:
            # Try to unwrap if wrapped
            if hasattr(func, "__wrapped__"):
                unwrapped = func.__wrapped__
                if not (
                    isinstance(unwrapped, MagicMock)
                    or hasattr(unwrapped, "return_value")
                    or hasattr(unwrapped, "side_effect")
                ):
                    return unwrapped
            # If not mocked, return directly (but wrap it to provide _module attribute)
            if not (
                isinstance(func, MagicMock)
                or hasattr(func, "return_value")
                or hasattr(func, "side_effect")
            ):

                def wrapped_configure_logging(*args, **kwargs):
                    return func(*args, **kwargs)

                wrapped_configure_logging._module = module
                wrapped_configure_logging._original_func = func
                return wrapped_configure_logging

    # Final fallback: import fresh after stopping all patches
    for _ in range(3):
        patch.stopall()
    import otel_init

    func = otel_init.configure_logging

    # Wrap it to provide _module attribute
    def wrapped_configure_logging(*args, **kwargs):
        return func(*args, **kwargs)

    wrapped_configure_logging._module = otel_init
    wrapped_configure_logging._original_func = func
    return wrapped_configure_logging


@pytest.fixture
def mock_mongodb_client():
    """Mock MongoDB client for testing."""
    with patch("pymongo.MongoClient") as mock_client:
        mock_db = Mock()
        mock_collection = Mock()
        mock_client.return_value = mock_db
        mock_db.__getitem__.return_value = mock_collection
        mock_collection.find_one.return_value = None
        mock_collection.insert_one.return_value = Mock(inserted_id="test_id")
        mock_collection.find.return_value = []
        yield mock_client


@pytest.fixture
def mock_nats_client():
    """Mock NATS client for testing."""
    with patch("nats.connect") as mock_connect:
        mock_client = AsyncMock()
        mock_connect.return_value = mock_client
        mock_client.publish = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.close = AsyncMock()
        yield mock_client


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for testing."""
    with patch("redis.Redis") as mock_redis:
        mock_client = Mock()
        mock_redis.return_value = mock_client
        mock_client.get.return_value = None
        mock_client.set.return_value = True
        mock_client.delete.return_value = 1
        yield mock_client


@pytest.fixture
def sample_signal_data():
    """Sample signal data for testing."""
    return {
        "signal_id": "test_signal_123",
        "symbol": "BTCUSDT",
        "action": "BUY",
        "confidence": 0.85,
        "strategy": "momentum_breakout",
        "timestamp": "2024-01-01T12:00:00Z",
        "metadata": {
            "price": 50000.0,
            "volume": 1.5,
            "stop_loss": 48000.0,
            "take_profit": 55000.0,
        },
    }


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        "order_id": "test_order_456",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": 0.001,
        "price": 50000.0,
        "time_in_force": "GTC",
        "status": "NEW",
    }


@pytest.fixture
def mock_binance_client():
    """Mock Binance client for testing."""
    with patch("tradeengine.exchange.binance.BinanceFuturesClient") as mock_client:
        mock_instance = Mock()
        mock_client.return_value = mock_instance
        mock_instance.create_order.return_value = {
            "orderId": 123456,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        mock_instance.get_account_info.return_value = {
            "totalWalletBalance": "10000.0",
            "availableBalance": "5000.0",
        }
        yield mock_instance


@pytest.fixture
def mock_environment_variables():
    """Mock environment variables for testing."""
    test_vars = {
        "MONGODB_URI": "mongodb://localhost:27017/test_db",
        "NATS_URL": "nats://localhost:4222",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "DEBUG",
    }

    with patch.dict(os.environ, test_vars):
        yield test_vars
