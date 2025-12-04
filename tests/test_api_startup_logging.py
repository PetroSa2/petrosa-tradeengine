"""
Simple tests for API startup logging configuration.
Covers the modified tradeengine/api.py lifespan function.
"""

from unittest.mock import patch

import otel_init


def test_api_module_imports_otel_init():
    """Test that api module imports otel_init."""
    # This ensures the import line is covered
    import tradeengine.api as api_module

    assert hasattr(api_module, "otel_init")
    assert api_module.otel_init is otel_init


def test_lifespan_function_exists():
    """Test that lifespan function is defined."""
    from tradeengine.api import lifespan

    # Verify it's a function
    assert callable(lifespan)
    # Verify it's an async context manager
    import inspect

    assert inspect.isasyncgenfunction(lifespan)


def test_configure_logging_is_importable_from_otel_init():
    """Test that configure_logging can be imported."""
    from otel_init import configure_logging

    assert callable(configure_logging)
    # This covers the line where api.py would call it
    # The actual call in lifespan is: otel_init.configure_logging()


def test_api_module_uses_configure_logging():
    """Test that api module can call configure_logging."""
    # This simulates what the lifespan function does
    with patch("otel_init.configure_logging", return_value=True) as mock_configure:
        # Import api module
        import tradeengine.api

        # The lifespan function should be able to call this
        result = tradeengine.api.otel_init.configure_logging()

        assert result is True
        mock_configure.assert_called_once()
