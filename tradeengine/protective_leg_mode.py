"""Protective-leg placement mode (#651).

``TE_PROTECTIVE_LEG_MODE`` selects how protective SL/TP legs are sent to
Binance:

- ``explicit_qty`` (default): conditional algo legs carry an explicit
  ``quantity`` equal to the live side position, ``positionSide`` and
  ``timeInForce=GTC``, and no ``closePosition``. On the Binance testnet a
  triggered ``closePosition=true`` leg executed "position + a hidden per-side
  residual", leaving sign-inverted hedge-mode positions (#566, #651). An
  explicit-quantity leg executes exactly as sent.
- ``close_position``: the pre-#651 behaviour (``closePosition=true`` +
  ``GTE_GTC``, no quantity), kept byte-for-byte as a rollback lever.

This module is deliberately dependency-free so the exchange layer can import it
without creating an import cycle.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

ProtectiveLegMode = Literal["explicit_qty", "close_position"]

EXPLICIT_QTY: ProtectiveLegMode = "explicit_qty"
CLOSE_POSITION: ProtectiveLegMode = "close_position"
DEFAULT_PROTECTIVE_LEG_MODE: ProtectiveLegMode = EXPLICIT_QTY

_VALID_MODES: frozenset[str] = frozenset({EXPLICIT_QTY, CLOSE_POSITION})
_warned_invalid: set[str] = set()


def protective_leg_mode() -> ProtectiveLegMode:
    """Return the effective protective-leg mode.

    Read on every call (not cached at import) so a test or an operator
    restart with a new env value takes effect without code changes. The
    environment variable wins over the ``Settings`` field; an unknown value
    falls back to the safe default (``explicit_qty``) with a one-time warning,
    because falling back to ``close_position`` would silently re-enable the
    residual over-close defect.
    """
    raw = os.getenv("TE_PROTECTIVE_LEG_MODE")
    if raw is None:
        try:
            from shared.config import settings

            raw = str(
                getattr(settings, "te_protective_leg_mode", DEFAULT_PROTECTIVE_LEG_MODE)
            )
        except Exception:  # pragma: no cover - settings import never fails in prod
            raw = DEFAULT_PROTECTIVE_LEG_MODE
    normalized = (raw or "").strip().lower()
    if normalized in _VALID_MODES:
        return normalized  # type: ignore[return-value]
    if normalized not in _warned_invalid:
        _warned_invalid.add(normalized)
        logger.warning(
            "TE_PROTECTIVE_LEG_MODE=%r is not one of %s; using %s (#651)",
            raw,
            sorted(_VALID_MODES),
            DEFAULT_PROTECTIVE_LEG_MODE,
        )
    return DEFAULT_PROTECTIVE_LEG_MODE


def is_explicit_qty_mode() -> bool:
    """True when protective legs are placed with an explicit quantity."""
    return protective_leg_mode() == EXPLICIT_QTY
