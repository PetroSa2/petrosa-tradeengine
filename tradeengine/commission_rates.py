"""The account's commission rate per symbol, read from the exchange and exposed on /state.

Costs in every EV and sizing decision need the rate the account really pays, which depends on its VIP tier
and on a fee-asset (BNB) discount. The rate is read per symbol at startup and then daily, cached in memory,
and a failed refresh keeps the last good value. Until a read has succeeded the fallback rates are reported,
labelled ``source: fallback`` (PetroSa2/petrosa_k8s#1239, recorded rule 16).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Reported (with ``source: fallback``) until the exchange read has succeeded for a symbol.
FALLBACK_TAKER_RATE = 0.0005
FALLBACK_MAKER_RATE = 0.0002
#: Binance's published discount on futures commissions when fees are paid in BNB (``feeBurn``).
FEE_BURN_DISCOUNT = 0.10
REFRESH_SECONDS = 24 * 60 * 60
#: A symbol whose read just failed is not re-read on demand for this long.
RETRY_SECONDS = 60


class CommissionRates:
    """Per-symbol maker and taker commission rates, refreshed daily from the exchange."""

    def __init__(self, exchange: Any = None, symbols: list[str] | None = None) -> None:
        self.exchange = exchange
        self.symbols = list(symbols or [])
        self._rates: dict[str, dict[str, Any]] = {}
        self._attempted: dict[str, float] = {}

    def configure(self, exchange: Any, symbols: list[str]) -> None:
        self.exchange = exchange
        self.symbols = list(symbols)

    async def refresh(self, symbol: str) -> bool:
        """Read one symbol's rate. A failure keeps the last good value and logs a warning."""
        self._attempted[symbol] = time.monotonic()
        try:
            maker, taker, fee_burn = await self.exchange.get_commission_rate(symbol)
        except Exception as exc:
            logger.warning("Commission rate refresh failed for %s: %s", symbol, exc)
            return False
        factor = 1.0 - FEE_BURN_DISCOUNT if fee_burn else 1.0
        self._rates[symbol] = {
            "taker_rate": taker * factor,
            "maker_rate": maker * factor,
            "source": "exchange",
            "fetched_at": datetime.now(UTC).isoformat(),
            "fee_burn": bool(fee_burn),
        }
        return True

    async def refresh_all(self) -> None:
        for symbol in self.symbols:
            await self.refresh(symbol)

    async def ensure(self, symbol: str) -> None:
        """Read a symbol that has no value yet (at most once a minute)."""
        if symbol in self._rates or self.exchange is None:
            return
        last = self._attempted.get(symbol)
        if last is not None and time.monotonic() - last < RETRY_SECONDS:
            return
        await self.refresh(symbol)

    def get(self, symbol: str) -> dict[str, Any]:
        """The symbol's commission as reported on ``/state``."""
        rate = self._rates.get(symbol)
        if rate is not None:
            return dict(rate)
        return {
            "taker_rate": FALLBACK_TAKER_RATE,
            "maker_rate": FALLBACK_MAKER_RATE,
            "source": "fallback",
            "fetched_at": None,
            "fee_burn": None,
        }

    async def run(self, interval_seconds: float = REFRESH_SECONDS) -> None:
        """Refresh at startup, then every ``interval_seconds``, until cancelled."""
        while True:
            await self.refresh_all()
            await asyncio.sleep(interval_seconds)


commission_rates = CommissionRates()
