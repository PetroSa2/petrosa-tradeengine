#!/usr/bin/env python3
"""
Application Launcher with Handler Monitoring

This script imports the handler monitoring first, then runs the normal
tradeengine application. This ensures we capture all logging handler
changes from the very beginning of the application lifecycle.
"""

import sys
import traceback
from datetime import datetime

# Add current directory to Python path
sys.path.insert(0, "/app")


def log_event(message: str):
    """Log events to stderr for immediate visibility"""
    timestamp = datetime.utcnow().isoformat()
    print(f"[{timestamp}] LAUNCHER: {message}", file=sys.stderr)


def main():
    """Main launcher function"""
    try:
        log_event("Starting application launcher with handler monitoring")

        # Step 1: Import and activate handler monitoring FIRST
        log_event("Step 1: Importing handler monitoring module")
        import debug_handler_lifecycle as monitor

        monitor.start_handler_monitoring()
        log_event("Handler monitoring activated")

        # Step 2: Get handler summary before any application imports
        log_event("Step 2: Getting initial handler state")
        initial_summary = monitor.get_handler_summary()
        log_event(f"Initial handler state: {initial_summary}")

        # Step 3: Import OpenTelemetry initialization
        log_event("Step 3: Importing OpenTelemetry initialization")

        log_event("OpenTelemetry module imported")

        # Step 4: Import and run the actual application
        log_event("Step 4: Importing tradeengine application")
        from tradeengine.api import app

        log_event("Tradeengine application imported")

        # Step 5: Get handler state after application import
        log_event("Step 5: Getting handler state after imports")
        post_import_summary = monitor.get_handler_summary()
        log_event(f"Post-import handler state: {post_import_summary}")

        # Step 6: Run the application
        log_event("Step 6: Starting application server")

        # Import uvicorn and run the app
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

    except Exception as e:
        log_event(f"FATAL ERROR: {e}")
        log_event(f"Stack trace: {traceback.format_exc()}")

        # Try to get final handler state even on error
        try:
            import debug_handler_lifecycle as monitor

            final_summary = monitor.get_handler_summary()
            log_event(f"Final handler state on error: {final_summary}")
        except Exception:
            log_event("Could not get final handler state")

        raise


if __name__ == "__main__":
    main()
