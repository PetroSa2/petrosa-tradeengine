"""
Ghost-position remediator (#592).

Operates on the ``ghost`` divergences emitted by
:class:`tradeengine.position_reconciler.PositionReconciler` (local tracker
holds a position, Binance shows nothing) and closes out the stale entry in
``PositionManager.positions`` — the raw local audit journal — with an audit
record.

This is the write-mode counterpart to the (deliberately read-only)
``PositionReconciler``, following the same detect-vs-remediate split already
established by :class:`tradeengine.naked_position_remediator.NakedPositionRemediator`
(#445): the reconciler only ever *observes* and reports; a dedicated
remediator takes the write action.

Design decision (per #592 PM review — "the correct outcome may be to void
the ghost, not to place the order"): a ghost divergence means Binance has
**no** position for this (symbol, side). Re-materialising it by placing a
real order would create a brand-new live position that never corresponded
to an actual fill — an active, dangerous default. Voiding the stale local
record is the safe, reversible-in-effect choice: it never touches the
exchange, and a restart reloads authoritative state from Data Manager /
the exchange truth store regardless. That decision is explicit and logged
here, not an implicit fallback.

Unlike ``NakedPositionRemediator`` (which places/cancels REAL exchange
orders and therefore ships off-by-default), this remediator's write action
has zero exchange-side blast radius, so it defaults to ``mode="void"``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from prometheus_client import Counter

from shared.audit import audit_logger

if TYPE_CHECKING:
    from tradeengine.position_manager import PositionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

ghost_positions_voided_total = Counter(
    "tradeengine_ghost_positions_voided_total",
    "Ghost (local-only, exchange-absent) journal entries voided by the "
    "remediator (#592)",
    ["symbol", "side"],
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

GhostRemediationMode = Literal["off", "dry_run", "void"]

_VOID_REASON = (
    "ghost_position_voided: absent from Binance positionRisk for this "
    "reconciliation cycle — journal-only entry closed as stale "
    "(#592, auto-reconciled; not re-materialised on the exchange)"
)


# ---------------------------------------------------------------------------
# GhostPositionRemediator
# ---------------------------------------------------------------------------


class GhostPositionRemediator:
    """Write-mode counterpart to :class:`PositionReconciler` for ``ghost``
    divergences.

    Injected as a dependency; the reconciler invokes :meth:`remediate` with
    the ``ghost``-category divergences after each detection pass, mirroring
    how ``NakedPositionRemediator.remediate`` is invoked for the
    ``unhedged``/``malformed_position`` categories.
    """

    def __init__(
        self,
        *,
        position_manager: PositionManager,
        mode: GhostRemediationMode = "void",
    ) -> None:
        self._position_manager = position_manager
        self._mode: GhostRemediationMode = self._coerce_mode(mode)

    @staticmethod
    def _coerce_mode(mode: str) -> GhostRemediationMode:
        normalized = (mode or "void").lower().strip()
        if normalized not in ("off", "dry_run", "void"):
            logger.warning(
                "GhostPositionRemediator: unknown mode %r; falling back to 'void'",
                mode,
            )
            return "void"
        return normalized  # type: ignore[return-value]

    @property
    def mode(self) -> GhostRemediationMode:
        return self._mode

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def remediate(
        self, ghost_divergences: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Void each ghost's stale raw-journal entry.

        Idempotent: a (symbol, side) already absent from
        ``position_manager.positions`` (voided on a prior pass, or never
        present to begin with) is a no-op — no second audit entry is ever
        written for the same key.

        Mutates each divergence dict in-place with a ``resolution`` key
        (``"voided"`` | ``"would_void"`` | ``"already_voided"``) so the
        caller can surface the action taken without a second lookup.
        Returns the subset of divergences actually voided this call.
        """
        voided: list[dict[str, Any]] = []

        for div in ghost_divergences:
            symbol = div["symbol"]
            side = div["side"]
            key = (symbol, side)

            if self._mode == "off":
                div["resolution"] = "skipped_mode_off"
                continue

            existing = self._position_manager.positions.get(key)
            if existing is None:
                # Already voided on a prior pass — idempotent no-op.
                div["resolution"] = "already_voided"
                continue

            if self._mode == "dry_run":
                div["resolution"] = "would_void"
                div["resolution_reason"] = _VOID_REASON
                logger.warning(
                    "GhostPositionRemediator[dry_run]: would void %s/%s qty=%s — %s",
                    symbol,
                    side,
                    existing.get("quantity", existing.get("amount")),
                    _VOID_REASON,
                )
                continue

            # mode == "void": explicit, logged, idempotent close-out of the
            # stale raw-journal entry. Never touches the exchange.
            del self._position_manager.positions[key]
            audit_logger.log_position(
                {**existing, "symbol": symbol, "position_side": side},
                status="voided_ghost",
            )
            logger.warning(
                "GhostPositionRemediator: voided ghost %s/%s qty=%s — %s",
                symbol,
                side,
                existing.get("quantity", existing.get("amount")),
                _VOID_REASON,
            )
            ghost_positions_voided_total.labels(symbol=symbol, side=side).inc()
            div["resolution"] = "voided"
            div["resolution_reason"] = _VOID_REASON
            voided.append(div)

        return voided
