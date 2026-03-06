"""
Regression test to verify the logging fix for 'unexpected keyword argument event'.
This test verifies that the logger now correctly supports structured logging
via keyword arguments and remains compatible with legacy f-string patterns.
"""

from shared.logger import get_logger


def test_fstring_logging_compatibility():
    """
    Test that f-strings containing 'event=' text are handled correctly as a single
    positional argument. This ensures that legacy log patterns containing
    structured-like text don't conflict with structlog's internal 'event' argument.
    """
    logger = get_logger("test_regression")
    symbol = "BTCUSDT"

    # This should succeed without 'multiple values for argument event' error
    logger.info(f"✅ Position updated | event=position_updated | symbol={symbol}")


def test_keyword_argument_structured_logging():
    """
    Test that the logger now properly supports real keyword arguments,
    fixing the regression where stdlib loggers received unexpected kwargs.
    """
    logger = get_logger("test_kwargs")

    # This would have failed with 'unexpected keyword argument' before the fix.
    # Now it should be handled correctly by structlog.
    logger.info("position_updated_event", symbol="BTCUSDT", price=50000.0)
