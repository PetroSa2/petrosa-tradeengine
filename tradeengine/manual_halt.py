"""Runtime state for the operator-controlled no-new-entries halt."""

import os
from typing import Any

from tradeengine.metrics import set_manual_halt_active

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def is_manual_halt_active() -> bool:
    active = os.getenv("TRADING_HALT", "false").strip().lower() in _TRUE_VALUES
    set_manual_halt_active(active)
    return active


def manual_halt_state() -> dict[str, Any]:
    active = is_manual_halt_active()
    return {
        "status": "healthy",
        "active": active,
        "source": "TRADING_HALT",
        "reason": "manual_halt" if active else None,
    }
