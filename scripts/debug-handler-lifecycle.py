#!/usr/bin/env python3
"""
Handler Lifecycle Debug Monitor

This script monkey-patches Python's logging system to track when handlers
are added, removed, or when logging configuration changes. It captures
stack traces to identify exactly what code is modifying logging handlers.

Usage: Import this module before any other application code to activate monitoring.
"""

import logging
import sys
import traceback
from datetime import datetime
from typing import Optional

# Global state
_original_addHandler = None
_original_removeHandler = None
_original_basicConfig = None
_debug_file = None
_monitoring_active = False


def log_debug_event(
    event_type: str, message: str, handler: Optional[logging.Handler] = None
):
    """Log debug events to file with stack trace"""
    global _debug_file

    if not _debug_file:
        _debug_file = open("/tmp/handler_lifecycle.log", "w")

    timestamp = datetime.utcnow().isoformat()
    handler_info = ""
    if handler:
        handler_info = f" (handler: {type(handler).__name__})"

    # Get current stack trace (skip this function and the caller)
    stack = traceback.format_stack()[:-2]  # Remove this function and caller
    stack_trace = "".join(stack)

    log_entry = f"""
{'='*80}
[{timestamp}] {event_type.upper()}: {message}{handler_info}
{'='*80}
Stack Trace:
{stack_trace}
Current handler count: {len(logging.getLogger().handlers)}
{'='*80}

"""

    _debug_file.write(log_entry)
    _debug_file.flush()

    # Also print to stderr for immediate visibility
    print(
        f"[{timestamp}] {event_type.upper()}: {message}{handler_info}", file=sys.stderr
    )


def tracked_addHandler(self, handler):
    """Tracked version of Logger.addHandler()"""
    global _original_addHandler

    log_debug_event("ADD_HANDLER", f"Adding handler to logger '{self.name}'", handler)

    # Call original method
    result = _original_addHandler(self, handler)

    log_debug_event("ADD_HANDLER_COMPLETE", f"Handler added to logger '{self.name}'")

    return result


def tracked_removeHandler(self, handler):
    """Tracked version of Logger.removeHandler()"""
    global _original_removeHandler

    log_debug_event(
        "REMOVE_HANDLER", f"Removing handler from logger '{self.name}'", handler
    )

    # Call original method
    result = _original_removeHandler(self, handler)

    log_debug_event(
        "REMOVE_HANDLER_COMPLETE", f"Handler removed from logger '{self.name}'"
    )

    return result


def tracked_basicConfig(*args, **kwargs):
    """Tracked version of logging.basicConfig()"""
    global _original_basicConfig

    # This is the most likely culprit for handler clearing
    log_debug_event(
        "BASIC_CONFIG_CALL",
        f"logging.basicConfig() called with args={args}, kwargs={kwargs}",
    )

    # Call original method
    result = _original_basicConfig(*args, **kwargs)

    log_debug_event("BASIC_CONFIG_COMPLETE", "logging.basicConfig() completed")

    return result


def track_logging_changes():
    """Track any direct manipulation of root logger handlers"""
    # For now, just log that we're monitoring
    # The addHandler/removeHandler monkey-patches should catch most cases
    log_debug_event("TRACKING", "Logging change tracking enabled")


def start_handler_monitoring():
    """Start monitoring logging handler changes"""
    global \
        _original_addHandler, \
        _original_removeHandler, \
        _original_basicConfig, \
        _monitoring_active

    if _monitoring_active:
        log_debug_event("MONITORING", "Handler monitoring already active")
        return

    log_debug_event("MONITORING", "Starting handler lifecycle monitoring")

    # Store original methods
    _original_addHandler = logging.Logger.addHandler
    _original_removeHandler = logging.Logger.removeHandler
    _original_basicConfig = logging.basicConfig

    # Apply monkey patches
    logging.Logger.addHandler = tracked_addHandler
    logging.Logger.removeHandler = tracked_removeHandler
    logging.basicConfig = tracked_basicConfig

    # Track direct handler manipulation
    track_logging_changes()

    _monitoring_active = True

    log_debug_event("MONITORING", "Handler monitoring activated successfully")


def stop_handler_monitoring():
    """Stop monitoring and restore original methods"""
    global \
        _original_addHandler, \
        _original_removeHandler, \
        _original_basicConfig, \
        _monitoring_active

    if not _monitoring_active:
        return

    log_debug_event("MONITORING", "Stopping handler lifecycle monitoring")

    # Restore original methods
    if _original_addHandler:
        logging.Logger.addHandler = _original_addHandler
    if _original_removeHandler:
        logging.Logger.removeHandler = _original_removeHandler
    if _original_basicConfig:
        logging.basicConfig = _original_basicConfig

    _monitoring_active = False

    log_debug_event("MONITORING", "Handler monitoring stopped")


def get_handler_summary():
    """Get current handler state summary"""
    root_logger = logging.getLogger()
    handlers = root_logger.handlers

    summary = {
        "count": len(handlers),
        "types": [type(h).__name__ for h in handlers],
        "levels": [h.level for h in handlers if hasattr(h, "level")],
    }

    log_debug_event("HANDLER_SUMMARY", f"Current handler state: {summary}")

    return summary


# Auto-start monitoring when module is imported
if __name__ != "__main__":
    start_handler_monitoring()
    log_debug_event("INIT", "Handler monitoring module imported and activated")
