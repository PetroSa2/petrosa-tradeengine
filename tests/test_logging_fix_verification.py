"""
Regression test to verify the logging fix for 'unexpected keyword argument event'.
This test ensures that using f-strings with 'event=' inside them doesn't cause
issues with the new structlog configuration.
"""

import logging

import pytest

from shared.logger import get_logger


def test_fstring_logging_with_event_text():
    """
    Test that f-strings containing 'event=' text are handled correctly as a single
    positional argument, even when using structlog.
    """
    logger = get_logger("test_regression")

    # This was the pattern in dispatcher.py that was failing when get_logger
    # returned a standard logger but some other part of the system tried
    # to use it as a structlog logger, OR if it was called with event= as a kwarg.
    # Now that we use structlog, this should be treated as the 'event' positional argument.
    symbol = "BTCUSDT"
    try:
        logger.info(f"✅ Position updated | event=position_updated | symbol={symbol}")
        success = True
    except Exception as e:
        success = False
        print(f"Logging failed with: {e}")

    assert success, "Logging with f-string containing 'event=' should not fail"


def test_real_keyword_argument_support():
    """
    Test that the logger now properly supports real keyword arguments,
    which was the project standard but was missing in tradeengine.
    """
    logger = get_logger("test_kwargs")

    try:
        # This would have failed with 'unexpected keyword argument' before the fix
        logger.info("position_updated_event", symbol="BTCUSDT", price=50000.0)
        success = True
    except Exception as e:
        success = False
        print(f"Logging with keyword arguments failed: {e}")

    assert success, "Logger should support real keyword arguments"
