import importlib
import logging
import sys
from unittest.mock import MagicMock, patch


def restore_otel_init_function(module):
    """Helper to restore original configure_logging function from module."""
    if not hasattr(module, "configure_logging"):
        return None

    func = getattr(module, "configure_logging", None)

    if func is None:
        return None

    is_mock = (
        isinstance(func, MagicMock)
        or hasattr(func, "return_value")
        or hasattr(func, "side_effect")
        or hasattr(func, "_mock_")
        or str(type(func).__name__)
        in ("MagicMock", "Mock", "AsyncMock", "NonCallableMagicMock")
    )

    if hasattr(func, "__wrapped__"):
        module.configure_logging = func.__wrapped__
        if hasattr(module.configure_logging, "return_value") or hasattr(
            module.configure_logging, "side_effect"
        ):
            is_mock = True
        else:
            return None

    if is_mock:
        try:
            module_file = getattr(module, "__file__", None)
            if module_file:
                if module_file.endswith(".py"):
                    importlib.reload(module)
                    new_func = getattr(module, "configure_logging", None)
                    if new_func:
                        if not hasattr(new_func, "return_value") and not hasattr(
                            new_func, "side_effect"
                        ):
                            return None
                    else:
                        return None
                else:
                    return None
            else:
                return None
        except Exception:
            return None


def restore_all_otel_init_patches():
    """Aggressively restore all otel_init.configure_logging patches.

    NOTE: We no longer call patch.stopall() at the top because it
    destroys all active patches including session-scoped mocks like
    mock_binance_global.  Instead we selectively restore only otel_init.
    """

    if "otel_init" in sys.modules:
        restore_otel_init_function(sys.modules["otel_init"])

    if "tradeengine.api" in sys.modules:
        api_module = sys.modules["tradeengine.api"]
        if hasattr(api_module, "otel_init"):
            otel_init_attr = getattr(api_module, "otel_init", None)
            if otel_init_attr and hasattr(otel_init_attr, "configure_logging"):
                func = getattr(otel_init_attr, "configure_logging", None)
                if func and (
                    hasattr(func, "return_value") or hasattr(func, "side_effect")
                ):
                    if "otel_init" in sys.modules:
                        original_func = getattr(
                            sys.modules["otel_init"], "configure_logging", None
                        )
                        if original_func:
                            if not hasattr(
                                original_func, "return_value"
                            ) and not hasattr(original_func, "side_effect"):
                                otel_init_attr.configure_logging = original_func

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
                            if "otel_init" in sys.modules:
                                original_func = getattr(
                                    sys.modules["otel_init"], "configure_logging", None
                                )
                                if original_func:
                                    if not hasattr(
                                        original_func, "return_value"
                                    ) and not hasattr(original_func, "side_effect"):
                                        otel_init_attr.configure_logging = original_func
            except Exception:
                pass


def cleanup_logging():
    """Cleanup logging and uvicorn loggers."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)

    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)
        logger.propagate = True

    if "otel_init" in sys.modules:
        module = sys.modules["otel_init"]
        if hasattr(module, "_global_logger_provider"):
            module._global_logger_provider = None
            return None

    return None
