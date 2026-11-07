"""Drawdown from the equity peak, including unrealized P&L, exposed on /state.

Recorded rule 5 of PetroSa2/petrosa_k8s#1239 sets drawdown steps relative to the equity peak, not to the day's
realized loss (``global_drawdown_pct`` stays exactly as it is, a separate backstop). Equity here is the wallet
balance plus unrealized P&L, sampled every minute. The peak is persisted through the data-manager API so it
survives a restart, and seeded at startup from data-manager's equity peak (``/api/v1/risk/inputs``) or the
stored value, whichever is higher. This module only exposes the drawdown; the reduce and halt steps are applied
in CIO.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "risk_equity_peak"
SCOPE = {"scope": "tradeengine"}
SAMPLE_SECONDS = 60
#: A new high is saved at most this often (the peak can rise on every sample in a rally).
SAVE_MIN_SECONDS = 60
RISK_INPUTS_URL = "/api/v1/risk/inputs"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number > 0 else None


def confirmed_no_history(body: Any) -> bool:
    """#839: data-manager answered that the ledger has no wallet days at all (not an error, not a gap)."""
    if not isinstance(body, dict):
        return False
    equity = body.get("equity")
    return (
        isinstance(equity, dict)
        and "error" not in equity
        and equity.get("available") is not False
        and equity.get("days") == 0
        and _number(equity.get("peak")) is None
    )


def peak_from_risk_inputs(body: Any) -> tuple[float, str | None] | None:
    """The equity peak (and its time) in a ``/api/v1/risk/inputs`` response, when it has one.

    Accepts ``{"equity": {"peak": <number>, "peak_at": <iso>}}`` and the flat
    ``{"equity_peak": <number>, "peak_at": <iso>}``.
    """
    if not isinstance(body, dict):
        return None
    equity = body.get("equity")
    source = equity if isinstance(equity, dict) else body
    peak = _number(source.get("peak", source.get("equity_peak")))
    if peak is None:
        return None
    at = source.get("peak_at")
    return peak, at if isinstance(at, str) else None


class EquityPeakTracker:
    """Tracks equity (wallet balance plus unrealized P&L), its peak and the drawdown from it."""

    def __init__(
        self,
        client: Any = None,
        exchange: Any = None,
        position_manager: Any = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.client = client
        self.exchange = exchange
        self.position_manager = position_manager
        self._clock = clock
        self.equity_now: float | None = None
        self.equity_peak: float | None = None
        self.peak_at: str | None = None
        self.as_of: str | None = None
        self._last_save: float | None = None
        self._unsaved = False
        # #839: until a source answers, the peak is unknown and so is the drawdown (never 0).
        self.seeded = False
        #: risk_inputs | stored | first_sample_fallback (labelled: confirmed empty history only)
        self.peak_source: str | None = None
        self._first_sample_allowed = False

    def configure(self, client: Any, exchange: Any, position_manager: Any) -> None:
        self.client = client
        self.exchange = exchange
        self.position_manager = position_manager

    # -- persistence -------------------------------------------------------------------------------
    async def seed(self) -> bool:
        """Start from the higher of data-manager's equity peak and the stored value.

        #839: returns whether the tracker is seeded. With neither source answering, the peak stays
        unknown (``from_peak_pct`` None, never 0) and ``sample`` retries. Only a confirmed empty
        history (data-manager answers with no ledger days and the store answers with no row) lets
        the first sample start the peak, as a labelled fallback.
        """
        candidates: list[tuple[float, str | None, str]] = []
        dm_empty = store_empty = False
        try:
            body = await self.client.request("GET", RISK_INPUTS_URL)
            found = peak_from_risk_inputs(body)
            if found:
                candidates.append((found[0], found[1], "risk_inputs"))
            dm_empty = confirmed_no_history(body)
        except Exception as exc:
            logger.info("No equity peak from %s (%s)", RISK_INPUTS_URL, exc)
        try:
            stored = await self.client.query(
                database="mongodb", collection=COLLECTION, filter=SCOPE, limit=1
            )
            rows = stored.get("data") or []
            if rows:
                peak = _number(rows[0].get("equity_peak"))
                if peak is not None:
                    candidates.append((peak, rows[0].get("peak_at"), "stored"))
            else:
                store_empty = True
        except Exception as exc:
            logger.warning("Could not read the stored equity peak: %s", exc)
        if candidates:
            self.equity_peak, self.peak_at, self.peak_source = max(
                candidates, key=lambda c: c[0]
            )
            self.seeded = True
            logger.info("Equity peak seeded at %s (%s)", self.equity_peak, self.peak_at)
            # A seed that beat the stored value is saved with the first new sample.
            self._unsaved = len(candidates) > 1
        elif dm_empty and store_empty:
            self._first_sample_allowed = True
            logger.warning(
                "EQUITY_PEAK_FALLBACK: data-manager reports no ledger history and no peak is stored; "
                "the first equity sample starts the peak (labelled first_sample_fallback)"
            )
        else:
            logger.warning(
                "EQUITY_PEAK_UNSEEDED: no equity peak from data-manager or the store; the drawdown "
                "is reported unknown until a source answers (retrying each sample)"
            )
        return self.seeded or self._first_sample_allowed

    async def _save(self) -> None:
        now = self._clock()
        if self._last_save is not None and now - self._last_save < SAVE_MIN_SECONDS:
            return
        self._last_save = now
        try:
            await self.client.upsert_one(
                database="mongodb",
                collection=COLLECTION,
                filter=SCOPE,
                record={
                    **SCOPE,
                    "equity_peak": self.equity_peak,
                    "peak_at": self.peak_at,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            self._unsaved = False
        except Exception as exc:
            self._unsaved = True
            logger.warning("Could not persist the equity peak: %s", exc)

    # -- sampling ----------------------------------------------------------------------------------
    async def sample(self) -> bool:
        """One equity sample: wallet balance plus unrealized P&L. A failure keeps the last value."""
        try:
            account = await self.exchange.get_account_info()
            wallet = float(account["total_wallet_balance"])
            unrealized = account.get("total_unrealized_profit")
            unrealized = (
                float(unrealized)
                if unrealized is not None
                else float(self.position_manager.get_total_unrealized_pnl())
            )
        except Exception as exc:
            logger.warning("Equity sample failed: %s", exc)
            return False
        self.equity_now = wallet + unrealized
        self.as_of = datetime.now(UTC).isoformat()
        if not self.seeded and not self._first_sample_allowed:
            await self.seed()
        if not self.seeded and not self._first_sample_allowed:
            return True  # equity known, peak unknown: the drawdown stays None
        if self.equity_peak is None:
            self.peak_source = "first_sample_fallback"
            self.seeded = True
        if self.equity_peak is None or self.equity_now > self.equity_peak:
            self.equity_peak = self.equity_now
            self.peak_at = self.as_of
            self._unsaved = True
        if self._unsaved:
            await self._save()
        return True

    async def run(self, interval_seconds: float = SAMPLE_SECONDS) -> None:
        """Seed, then sample every ``interval_seconds`` until cancelled."""
        await self.seed()
        while True:
            await self.sample()
            await asyncio.sleep(interval_seconds)

    # -- /state ------------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        """``/state`` ``drawdown``: equity, peak, drawdown from the peak and net notional over equity."""
        now, peak = self.equity_now, self.equity_peak
        from_peak = (
            max(0.0, (peak - now) / peak)
            if self.seeded and now is not None and peak
            else None
        )
        net_ratio = None
        if now and now > 0 and self.position_manager is not None:
            try:
                net_ratio = self.position_manager.get_notional_summary()[1] / now
            except Exception as exc:
                logger.warning("Net notional unavailable: %s", exc)
        return {
            "equity_now": now,
            "equity_peak": peak,
            "peak_at": self.peak_at,
            "from_peak_pct": from_peak,
            "net_notional_ratio": net_ratio,
            "as_of": self.as_of,
            "seeded": self.seeded,
            "peak_source": self.peak_source,
        }


equity_peak_tracker = EquityPeakTracker()
