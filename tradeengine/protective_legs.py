"""Protective-leg lifecycle manager (#651).

With ``TE_PROTECTIVE_LEG_MODE=explicit_qty`` protective SL/TP legs are
conditional algo orders with an explicit ``quantity`` equal to the live side
position (``symbol`` + ``positionSide``). Unlike ``closePosition=true`` legs,
explicit-quantity legs neither follow the position size nor auto-expire when
the side closes, so this manager keeps them converged to Binance truth:

- **Resize**: when the side quantity changes (an entry consolidating into the
  side, a partial close, a scale-out, a manual trade), each leg kind is
  cancelled and re-placed at the new quantity with the SAME trigger price.
  The old leg is cancelled BEFORE the new one is placed, so the legs of one
  kind never add up to more than the side holds at any instant (an
  over-covering pair of legs is exactly what inverts a side if both fire).
- **Flat**: when the side reaches 0, every leg on that side is cancelled
  (legs younger than ``flat_grace_sec`` are spared, because positionRisk can
  lag a fresh entry fill by a few seconds).
- **Duplicates**: at most one explicit leg per kind survives on a side.
- **Inverted** (LONG with positionAmt < 0 / SHORT with positionAmt > 0): the
  manager NEVER places anything. It raises a CRITICAL log + metric + alert
  and cancels the side's legs (a closing leg on an inverted side can only
  deepen the inversion). It never trades a correction — a counter-trade
  leaves the testnet residual in place (#566).
- **Legacy migration** (explicit_qty mode only): a ``closePosition`` leg on a
  healthy side is replaced by an explicit-quantity leg at the same trigger —
  the replacement is placed first and the legacy leg is cancelled only once
  the replacement is live, so a failed placement never leaves the side naked.

The manager reads Binance truth (positionRisk + openAlgoOrders) on every
decision; local state is only used to prefer the leg an OCO pair tracks and to
keep that tracking up to date. It coordinates with ``OCOManager`` through the
per-side lock, and it never touches the legs of a tracked OCO pair whose
sibling leg has just disappeared — that is a fill the OCO monitor owns.

Triggers: event-driven ``request_sync`` calls (entry/close/fill paths and the
user-data stream's ACCOUNT_UPDATE), follow-up passes a few seconds later for
REST propagation lag, a periodic full sweep and a startup sweep.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from tradeengine.protective_leg_mode import EXPLICIT_QTY, protective_leg_mode

logger = logging.getLogger(__name__)

SL_KIND = "SL"
TP_KIND = "TP"
_KINDS = (SL_KIND, TP_KIND)
_HEDGE_SIDES = ("LONG", "SHORT")

# Binance cancel answers meaning "that algo order is no longer open". Only
# meaningful when the correct endpoint for the leg kind returned them (#650).
_GONE_CODES = frozenset({-2011, -2013, -4029})
_GONE_MARKERS = ("unknown order", "does not exist", "order not found")

_QTY_EPSILON = 1e-12
_DEFAULT_FOLLOWUPS: tuple[float, ...] = (0.0, 3.0, 10.0)
_MID_FILL_DEFER_LIMIT_SEC = 60.0
_STARTUP_SWEEP_TIMEOUT_SEC = 30.0
_MIGRATE_RETRY_SEC = 300.0

LegReplacedCallback = Callable[[str, str, str, str, str], Awaitable[None]]


def leg_kind_of(order: dict[str, Any]) -> str | None:
    """``SL`` / ``TP`` for a protective conditional order, else ``None``.

    ``/openAlgoOrders`` reports the kind in ``orderType`` (#562/#594);
    standard order payloads use ``type``.
    """
    raw = str(
        order.get("orderType") or order.get("type") or order.get("origType") or ""
    ).upper()
    if raw in ("STOP_MARKET", "STOP"):
        return SL_KIND
    if raw in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
        return TP_KIND
    return None


def closing_side_for(position_side: str) -> str:
    """Binance side of an order that reduces ``position_side`` in hedge mode."""
    return "SELL" if position_side.upper() == "LONG" else "BUY"


def is_inverted(position_side: str, position_amt: float) -> bool:
    """Hedge-mode sign inversion: LONG with amt < 0 or SHORT with amt > 0."""
    side = position_side.upper()
    if side == "LONG":
        return position_amt < 0
    if side == "SHORT":
        return position_amt > 0
    return False


def is_gone_error(exc: BaseException) -> bool:
    """True when a cancel failed because the order is already gone."""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _GONE_CODES:
        return True
    text = str(exc)
    if any(f"code={c}" in text for c in _GONE_CODES):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _GONE_MARKERS)


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ProtectiveLeg:
    """One open protective algo leg on a hedge-mode side."""

    algo_id: str
    kind: str
    order_type: str
    side: str
    position_side: str
    quantity: float
    trigger_price: float | None
    limit_price: float | None
    close_position: bool
    status: str
    create_time_ms: float | None

    @classmethod
    def from_order(cls, order: dict[str, Any]) -> ProtectiveLeg | None:
        kind = leg_kind_of(order)
        algo_id = order.get("algoId")
        if kind is None or algo_id is None:
            return None
        order_type = str(
            order.get("orderType") or order.get("type") or order.get("origType") or ""
        ).upper()
        limit_price = None
        if order_type in ("STOP", "TAKE_PROFIT"):
            limit_price = _to_float(order.get("price")) or None
        return cls(
            algo_id=str(algo_id),
            kind=kind,
            order_type=order_type,
            side=str(order.get("side", "")).upper(),
            position_side=str(order.get("positionSide", "BOTH")).upper(),
            quantity=_to_float(order.get("quantity") or order.get("origQty")) or 0.0,
            trigger_price=_to_float(order.get("triggerPrice") or order.get("stopPrice"))
            or None,
            limit_price=limit_price,
            close_position=order.get("closePosition") in (True, "true", "True"),
            status=str(order.get("algoStatus") or order.get("status") or "NEW").upper(),
            create_time_ms=_to_float(
                order.get("createTime") or order.get("bookTime") or order.get("time")
            ),
        )


def side_legs(
    orders: Iterable[dict[str, Any]], symbol: str, position_side: str
) -> list[ProtectiveLeg]:
    """Protective closing algo legs of one hedge-mode side."""
    want_side = closing_side_for(position_side)
    legs: list[ProtectiveLeg] = []
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("symbol", "")).upper() != symbol.upper():
            continue
        leg = ProtectiveLeg.from_order(order)
        if leg is None:
            continue
        if leg.position_side != position_side.upper() or leg.side != want_side:
            continue
        legs.append(leg)
    return legs


def position_amt_of(
    rows: Iterable[dict[str, Any]], symbol: str, position_side: str
) -> float:
    """Signed positionAmt of a hedge-mode side from positionRisk rows (0 if absent)."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol", "")).upper() != symbol.upper():
            continue
        if str(row.get("positionSide", "BOTH")).upper() != position_side.upper():
            continue
        return _to_float(row.get("positionAmt")) or 0.0
    return 0.0


class ProtectiveLegManager:
    """Keeps explicit-quantity protective legs converged to the side (#651)."""

    def __init__(
        self,
        exchange: Any,
        oco_manager: Any = None,
        *,
        logger_: logging.Logger | None = None,
        sync_interval_sec: float | None = None,
        flat_grace_sec: float | None = None,
        migrate_legacy: bool | None = None,
        on_leg_replaced: LegReplacedCallback | None = None,
        inversion_check_delays: Iterable[float] = (0.5, 2.0, 5.0),
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        from shared.config import settings

        self._exchange = exchange
        self._oco = oco_manager
        self._log = logger_ or logger
        self._interval = float(
            sync_interval_sec
            if sync_interval_sec is not None
            else getattr(settings, "te_protective_leg_sync_interval_sec", 30.0)
        )
        self._flat_grace = float(
            flat_grace_sec
            if flat_grace_sec is not None
            else getattr(settings, "te_protective_leg_flat_grace_sec", 30.0)
        )
        self._migrate_legacy = bool(
            migrate_legacy
            if migrate_legacy is not None
            else getattr(settings, "te_protective_leg_migrate_legacy", True)
        )
        self._on_leg_replaced = on_leg_replaced
        self._inversion_check_delays = tuple(inversion_check_delays)
        self._clock = clock
        self._wall_clock = wall_clock
        self._own_locks: dict[str, asyncio.Lock] = {}
        # (symbol, side) -> pending due times (monotonic seconds)
        self._pending: dict[tuple[str, str], list[float]] = {}
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[Any] | None = None
        self._running = False
        self._background: set[asyncio.Task[Any]] = set()
        # latched per inversion episode; cleared once the side reads non-inverted
        self._inversion_alerted: set[tuple[str, str]] = set()
        self._flat_first_seen: dict[tuple[str, str], float] = {}
        self._mid_fill_first_seen: dict[tuple[str, str, str], float] = {}
        self._migrate_failed_at: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, *, startup_sweep: bool = True) -> None:
        """Run the startup sweep (awaited, bounded) and start the loop."""
        if self._running or self._exchange is None:
            return
        self._running = True
        self._wake = asyncio.Event()
        if startup_sweep:
            try:
                await asyncio.wait_for(
                    self.sync_all(reason="startup"),
                    timeout=_STARTUP_SWEEP_TIMEOUT_SEC,
                )
            except Exception:
                self._log.exception(
                    "#651: protective-leg startup sweep failed (non-fatal); the "
                    "periodic sweep will retry"
                )
        self._task = asyncio.create_task(self._run(), name="protective-leg-manager")
        self._log.info(
            "#651: protective-leg manager started (mode=%s, interval=%ss, "
            "flat_grace=%ss, migrate_legacy=%s)",
            protective_leg_mode(),
            self._interval,
            self._flat_grace,
            self._migrate_legacy,
        )

    async def stop(self) -> None:
        self._running = False
        tasks = [t for t in (self._task, *self._background) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._background.clear()

    def request_sync(
        self,
        symbol: str | None,
        position_side: str | None,
        reason: str = "",
        delays: Iterable[float] = _DEFAULT_FOLLOWUPS,
    ) -> None:
        """Schedule side syncs (non-blocking, idempotent).

        The default follow-ups (now, +3 s, +10 s) absorb positionRisk
        propagation lag after a fill: every pass re-reads Binance truth, so an
        extra pass is a no-op once the side has converged.
        """
        if not symbol or not position_side or not self._running:
            return
        side = str(position_side).upper()
        if side not in _HEDGE_SIDES:
            return
        key = (str(symbol).upper(), side)
        now = self._clock()
        due = self._pending.setdefault(key, [])
        for delay in delays:
            due.append(now + max(0.0, float(delay)))
        due.sort()
        self._log.debug("#651: leg sync requested for %s %s (%s)", *key, reason)
        if self._wake is not None:
            self._wake.set()

    def on_protective_fill(
        self,
        symbol: str | None,
        position_side: str | None,
        filled_order_id: str | None = None,
    ) -> None:
        """A protective leg filled: run the inversion guard + a side sync.

        Non-blocking — safe to call from the OCO monitor while it holds the
        side lock.
        """
        if not symbol or not position_side:
            return
        side = str(position_side).upper()
        if side not in _HEDGE_SIDES:
            return
        self.request_sync(symbol, side, reason="protective_fill")
        try:
            task = asyncio.get_running_loop().create_task(
                self.check_inversion_after_fill(
                    str(symbol).upper(), side, filled_order_id=filled_order_id
                )
            )
        except RuntimeError:
            return
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run(self) -> None:
        assert self._wake is not None
        next_full = self._clock() + self._interval
        while self._running:
            try:
                now = self._clock()
                due_keys: list[tuple[str, str]] = []
                for key, times in list(self._pending.items()):
                    if times and times[0] <= now:
                        self._pending[key] = [t for t in times if t > now]
                        due_keys.append(key)
                    if not self._pending.get(key):
                        self._pending.pop(key, None)
                for symbol, side in due_keys:
                    await self.sync_side(symbol, side, reason="scheduled")
                if self._clock() >= next_full:
                    await self.sync_all(reason="periodic")
                    next_full = self._clock() + self._interval
                wait = next_full - self._clock()
                upcoming = [t[0] for t in self._pending.values() if t]
                if upcoming:
                    wait = min(wait, min(upcoming) - self._clock())
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, wait))
                except TimeoutError:
                    pass
                self._wake.clear()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("#651: protective-leg manager loop error")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Truth reads
    # ------------------------------------------------------------------

    async def _read_positions(self) -> list[dict[str, Any]] | None:
        try:
            rows = await self._exchange.get_position_info()
        except Exception as exc:
            self._log.warning("#651: positionRisk read failed: %s", exc)
            return None
        return list(rows) if isinstance(rows, list | tuple) else None

    async def _read_orders(
        self, symbol: str | None = None
    ) -> list[dict[str, Any]] | None:
        try:
            if symbol is None:
                orders = await self._exchange.get_open_algo_orders()
            else:
                orders = await self._exchange.get_open_algo_orders(symbol=symbol)
        except Exception as exc:
            self._log.warning("#651: openAlgoOrders read failed (%s): %s", symbol, exc)
            return None
        return list(orders) if isinstance(orders, list | tuple) else None

    async def _read_side_amt(self, symbol: str, position_side: str) -> float | None:
        rows = await self._read_positions()
        if rows is None:
            return None
        return position_amt_of(rows, symbol, position_side)

    def _target_quantity(self, symbol: str, quantity: float) -> float:
        floor = getattr(self._exchange, "_floor_quantity_to_step", None)
        if callable(floor):
            try:
                return float(floor(symbol, quantity))
            except Exception:
                return float(quantity)
        return float(quantity)

    # ------------------------------------------------------------------
    # Coordination with OCOManager
    # ------------------------------------------------------------------

    def _lock_for(self, key: str) -> asyncio.Lock:
        side_lock = getattr(self._oco, "side_lock", None)
        if callable(side_lock):
            lock = side_lock(key)
            if isinstance(lock, asyncio.Lock):
                return lock
        return self._own_locks.setdefault(key, asyncio.Lock())

    def _tracked_pairs(self, key: str) -> list[dict[str, Any]]:
        pairs = getattr(self._oco, "active_oco_pairs", None)
        if not isinstance(pairs, dict):
            return []
        entries = pairs.get(key) or []
        if isinstance(entries, dict):
            entries = [entries]
        return [
            p for p in entries if isinstance(p, dict) and p.get("status") == "active"
        ]

    def _tracked_ids(self, key: str) -> set[str]:
        ids: set[str] = set()
        for pair in self._tracked_pairs(key):
            for field_name in ("sl_order_id", "tp_order_id"):
                if pair.get(field_name):
                    ids.add(str(pair[field_name]))
        return ids

    def _pair_mid_fill(self, key: str, open_ids: set[str]) -> bool:
        """True while a tracked pair has exactly one leg open (a fill the OCO
        monitor is about to process). Bounded so a stuck pair cannot block the
        side forever."""
        now = self._clock()
        busy = False
        for pair in self._tracked_pairs(key):
            if pair.get("orphaned"):
                continue
            sl_id = str(pair.get("sl_order_id") or "")
            tp_id = str(pair.get("tp_order_id") or "")
            if not sl_id or not tp_id:
                continue
            if (sl_id in open_ids) == (tp_id in open_ids):
                self._mid_fill_first_seen.pop((key, sl_id, tp_id), None)
                continue
            first = self._mid_fill_first_seen.setdefault((key, sl_id, tp_id), now)
            if now - first < _MID_FILL_DEFER_LIMIT_SEC:
                busy = True
            else:
                self._log.warning(
                    "#651: OCO pair %s/%s on %s has been half-filled for %.0fs — "
                    "leg sync proceeding without the OCO monitor",
                    sl_id,
                    tp_id,
                    key,
                    now - first,
                )
        return busy

    @staticmethod
    def _choose_keep(legs: list[ProtectiveLeg]) -> ProtectiveLeg:
        """The leg of a kind that survives deduplication: the oldest one (then
        the lowest algoId). Deterministic from exchange data alone, so two
        engine processes can never each keep a different duplicate and cancel
        the other's; OCO pairs tracking a cancelled duplicate are re-pointed."""

        def order_key(leg: ProtectiveLeg) -> tuple[float, int]:
            try:
                algo_id = int(leg.algo_id)
            except ValueError:
                algo_id = 0
            created = leg.create_time_ms
            return (created if created is not None else float("inf"), algo_id)

        return min(legs, key=order_key)

    async def _replace_tracked_id(
        self, symbol: str, side: str, kind: str, old_id: str, new_id: str
    ) -> None:
        key = f"{symbol}_{side}"
        field_name = "sl_order_id" if kind == SL_KIND else "tp_order_id"
        algo_flag = "sl_is_algo" if kind == SL_KIND else "tp_is_algo"
        for pair in self._tracked_pairs(key):
            if str(pair.get(field_name) or "") == old_id:
                pair[field_name] = new_id
                pair[algo_flag] = True
        if self._on_leg_replaced is not None:
            try:
                await self._on_leg_replaced(symbol, side, kind, old_id, new_id)
            except Exception:
                self._log.exception(
                    "#651: leg-replaced callback failed for %s %s %s %s->%s",
                    symbol,
                    side,
                    kind,
                    old_id,
                    new_id,
                )

    def _mark_pairs_cancelled(self, key: str, gone_ids: set[str], reason: str) -> None:
        for pair in self._tracked_pairs(key):
            ids = {
                str(pair.get(f) or "")
                for f in ("sl_order_id", "tp_order_id")
                if pair.get(f)
            }
            if ids and ids <= gone_ids:
                pair["status"] = "cancelled"
                pair["close_reason"] = reason

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _record(self, action: str, outcome: str) -> None:
        try:
            from tradeengine.metrics import (
                otel_protective_leg_sync_actions,
                protective_leg_sync_actions_total,
            )

            protective_leg_sync_actions_total.labels(
                action=action, outcome=outcome
            ).inc()
            otel_protective_leg_sync_actions.add(
                1, {"action": action, "outcome": outcome}
            )
        except Exception:  # pragma: no cover - metrics never break the sync
            pass

    async def _cancel_leg(self, symbol: str, leg: ProtectiveLeg, action: str) -> str:
        """Cancel one algo leg via the algo endpoint. Returns cancelled|gone|failed."""
        try:
            await self._exchange.cancel_algo_order(symbol, leg.algo_id)
        except Exception as exc:
            if is_gone_error(exc):
                self._log.info(
                    "#651: %s leg %s on %s %s already gone (%s)",
                    leg.kind,
                    leg.algo_id,
                    symbol,
                    leg.position_side,
                    exc,
                )
                return "gone"
            self._log.error(
                "#651: failed to cancel %s leg %s on %s %s (%s): %s",
                leg.kind,
                leg.algo_id,
                symbol,
                leg.position_side,
                action,
                exc,
            )
            self._record(action, "failed")
            return "failed"
        self._log.warning(
            "#651: cancelled %s leg %s (qty=%s, closePosition=%s) on %s %s — %s",
            leg.kind,
            leg.algo_id,
            leg.quantity,
            leg.close_position,
            symbol,
            leg.position_side,
            action,
        )
        self._record(action, "success")
        return "cancelled"

    async def _place_like(
        self, symbol: str, leg: ProtectiveLeg, quantity: float
    ) -> str:
        """Place an explicit-quantity leg mirroring ``leg`` (same kind, type,
        trigger, limit price). Returns the new algo id; raises on failure."""
        if leg.trigger_price is None:
            raise ValueError(f"leg {leg.algo_id} has no trigger price")
        result = await self._exchange.place_protective_leg(
            symbol=symbol,
            position_side=leg.position_side,
            side=leg.side,
            order_type=leg.order_type,
            quantity=quantity,
            trigger_price=leg.trigger_price,
            limit_price=leg.limit_price,
        )
        new_id = result.get("algoId") or result.get("orderId")
        if new_id is None:
            raise RuntimeError(f"placement returned no algoId: {result}")
        return str(new_id)

    async def _alert(self, name: str, payload: dict[str, Any]) -> None:
        try:
            from tradeengine.services.alert_publisher import alert_publisher

            await alert_publisher.publish(
                alert_name=name, severity="critical", payload=payload
            )
        except Exception:  # pragma: no cover - alert path never raises
            self._log.debug("#651: alert publish failed for %s", name, exc_info=True)

    async def _report_inversion(
        self,
        symbol: str,
        side: str,
        position_amt: float,
        *,
        source: str,
        filled_order_id: str | None = None,
    ) -> None:
        """CRITICAL log + metric + alert for a sign-inverted side. Latched per
        episode. NEVER places, cancels or modifies an order."""
        key = (symbol, side)
        if key in self._inversion_alerted:
            return
        self._inversion_alerted.add(key)
        self._log.critical(
            "🚨 #651 SIGN-INVERTED SIDE: %s %s positionAmt=%s after %s "
            "(filled_order_id=%s). Protective legs will NOT be placed on this "
            "side and NO corrective trade is sent (a counter-trade leaves the "
            "testnet residual in place, #566). Operator action required.",
            symbol,
            side,
            position_amt,
            source,
            filled_order_id,
        )
        try:
            from tradeengine.metrics import (
                otel_protective_fill_inversion,
                protective_fill_inversion_total,
            )

            protective_fill_inversion_total.labels(
                symbol=symbol, side=side, source=source
            ).inc()
            otel_protective_fill_inversion.add(
                1, {"symbol": symbol, "side": side, "source": source}
            )
        except Exception:  # pragma: no cover
            pass
        await self._alert(
            f"protective_fill_inversion.{symbol}",
            {
                "symbol": symbol,
                "position_side": side,
                "position_amt": position_amt,
                "source": source,
                "filled_order_id": filled_order_id,
                "action": "none — operator must flatten; no auto-correction",
            },
        )

    async def check_inversion_after_fill(
        self,
        symbol: str,
        position_side: str,
        *,
        filled_order_id: str | None = None,
        delays: Iterable[float] | None = None,
    ) -> bool:
        """Post-fill inversion guard (#651 scope 3). Read-only.

        Re-reads the side a few times (positionRisk can lag the fill) and
        raises the CRITICAL alert on the first inverted reading. Returns True
        when an inversion was detected. Never places, cancels or modifies an
        order.
        """
        side = position_side.upper()
        for delay in self._inversion_check_delays if delays is None else delays:
            if delay > 0:
                await asyncio.sleep(delay)
            amt = await self._read_side_amt(symbol, side)
            if amt is None:
                continue
            if is_inverted(side, amt):
                await self._report_inversion(
                    symbol,
                    side,
                    amt,
                    source="protective_fill",
                    filled_order_id=filled_order_id,
                )
                return True
            if abs(amt) <= _QTY_EPSILON:
                return False
        return False

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def _needs_attention(
        self,
        symbol: str,
        side: str,
        position_amt: float,
        legs: list[ProtectiveLeg],
    ) -> bool:
        explicit_mode = protective_leg_mode() == EXPLICIT_QTY
        managed = [leg for leg in legs if explicit_mode or not leg.close_position]
        if is_inverted(side, position_amt):
            return bool(legs) or (symbol, side) not in self._inversion_alerted
        if abs(position_amt) <= _QTY_EPSILON:
            return bool(managed)
        target = self._target_quantity(symbol, abs(position_amt))
        for kind in _KINDS:
            explicit = [
                leg for leg in legs if leg.kind == kind and not leg.close_position
            ]
            legacy = [leg for leg in legs if leg.kind == kind and leg.close_position]
            if len(explicit) > 1:
                return True
            if explicit and abs(explicit[0].quantity - target) > _QTY_EPSILON:
                return True
            if explicit_mode and legacy and (explicit or self._migrate_legacy):
                return True
        return False

    def _prune_state(self) -> None:
        """Drop stale bookkeeping (pairs the OCO monitor has since completed,
        migration failures past their back-off)."""
        now = self._clock()
        self._migrate_failed_at = {
            k: t
            for k, t in self._migrate_failed_at.items()
            if now - t < 2 * _MIGRATE_RETRY_SEC
        }
        self._mid_fill_first_seen = {
            k: t
            for k, t in self._mid_fill_first_seen.items()
            if now - t < 10 * _MID_FILL_DEFER_LIMIT_SEC
        }

    async def sync_all(self, reason: str = "periodic") -> dict[str, int]:
        """Sweep every side holding a position or a protective leg."""
        counts = {"checked": 0, "synced": 0}
        if self._exchange is None:
            return counts
        self._prune_state()
        rows = await self._read_positions()
        orders = await self._read_orders()
        if rows is None or orders is None:
            return counts
        candidates: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            side = str(row.get("positionSide", "BOTH")).upper()
            amt = _to_float(row.get("positionAmt")) or 0.0
            if side in _HEDGE_SIDES and abs(amt) > _QTY_EPSILON:
                candidates.add((str(row.get("symbol", "")).upper(), side))
        for order in orders:
            if not isinstance(order, dict) or leg_kind_of(order) is None:
                continue
            side = str(order.get("positionSide", "BOTH")).upper()
            if side in _HEDGE_SIDES:
                candidates.add((str(order.get("symbol", "")).upper(), side))
        for symbol, side in sorted(candidates):
            counts["checked"] += 1
            legs = side_legs(orders, symbol, side)
            amt = position_amt_of(rows, symbol, side)
            if not self._needs_attention(symbol, side, amt, legs):
                if not is_inverted(side, amt):
                    self._inversion_alerted.discard((symbol, side))
                continue
            counts["synced"] += 1
            try:
                await self.sync_side(symbol, side, reason=reason)
            except Exception:
                self._log.exception("#651: leg sync failed for %s %s", symbol, side)
        return counts

    async def sync_side(
        self, symbol: str, position_side: str, reason: str = "manual"
    ) -> dict[str, Any]:
        """Converge one hedge-mode side's protective legs to Binance truth."""
        side = str(position_side).upper()
        if self._exchange is None or side not in _HEDGE_SIDES:
            return {"status": "skipped", "reason": "not_managed"}
        symbol = str(symbol).upper()
        async with self._lock_for(f"{symbol}_{side}"):
            return await self._sync_side_locked(symbol, side, reason)

    async def _sync_side_locked(
        self, symbol: str, side: str, reason: str
    ) -> dict[str, Any]:
        key = f"{symbol}_{side}"
        amt = await self._read_side_amt(symbol, side)
        orders = await self._read_orders(symbol)
        if amt is None or orders is None:
            return {"status": "skipped", "reason": "truth_unavailable"}
        legs = side_legs(orders, symbol, side)
        open_ids = {
            str(o.get("algoId"))
            for o in orders
            if isinstance(o, dict) and o.get("algoId") is not None
        }
        if not is_inverted(side, amt):
            self._inversion_alerted.discard((symbol, side))
        if abs(amt) > _QTY_EPSILON:
            self._flat_first_seen.pop((symbol, side), None)

        if any(leg.status not in ("NEW", "") for leg in legs):
            # A leg is triggering: the side is about to change. Look again soon.
            self.request_sync(symbol, side, reason="leg_triggering", delays=(3.0,))
            return {"status": "deferred", "reason": "leg_triggering"}
        if self._pair_mid_fill(key, open_ids):
            self.request_sync(symbol, side, reason="oco_mid_fill", delays=(5.0,))
            return {"status": "deferred", "reason": "oco_fill_in_progress"}

        explicit_mode = protective_leg_mode() == EXPLICIT_QTY
        # On a flat side, legacy closePosition legs are left to Binance in
        # close_position mode (GTE_GTC sweeps them, as before #651).
        managed = [leg for leg in legs if explicit_mode or not leg.close_position]
        actions: list[str] = []

        if is_inverted(side, amt):
            await self._report_inversion(symbol, side, amt, source=f"leg_sync:{reason}")
            # Every closing leg on an inverted side — explicit or closePosition,
            # in either mode — can only deepen the inversion, and GTE_GTC does
            # not sweep it (the position is not zero): the DOTUSDT orphan SL.
            gone: set[str] = set()
            for leg in legs:
                outcome = await self._cancel_leg(symbol, leg, "cancel_inverted")
                if outcome != "failed":
                    gone.add(leg.algo_id)
                    actions.append(f"cancel_inverted:{leg.algo_id}")
            self._mark_pairs_cancelled(
                key, gone | (self._tracked_ids(key) - open_ids), "side_inverted"
            )
            return {"status": "inverted", "position_amt": amt, "actions": actions}

        if abs(amt) <= _QTY_EPSILON:
            return await self._handle_flat(symbol, side, managed, open_ids, actions)

        target = self._target_quantity(symbol, abs(amt))
        for kind in _KINDS:
            explicit = [
                leg for leg in legs if leg.kind == kind and not leg.close_position
            ]
            legacy = [leg for leg in legs if leg.kind == kind and leg.close_position]
            if explicit:
                keep = self._choose_keep(explicit)
                # A cancelled duplicate may be what an OCO pair tracks: point
                # the pair at the surviving leg, otherwise the OCO monitor
                # would read the vanished id as a fill.
                for extra in explicit:
                    if extra is keep:
                        continue
                    if (
                        await self._cancel_leg(symbol, extra, "cancel_duplicate")
                        != "failed"
                    ):
                        actions.append(f"cancel_duplicate:{extra.algo_id}")
                        await self._replace_tracked_id(
                            symbol, side, kind, extra.algo_id, keep.algo_id
                        )
                if explicit_mode:
                    # An explicit leg already covers the side; a legacy
                    # closePosition leg next to it would over-close by the
                    # residual if both fired.
                    for old in legacy:
                        if (
                            await self._cancel_leg(symbol, old, "cancel_duplicate")
                            != "failed"
                        ):
                            actions.append(f"cancel_legacy:{old.algo_id}")
                            await self._replace_tracked_id(
                                symbol, side, kind, old.algo_id, keep.algo_id
                            )
                if abs(keep.quantity - target) > _QTY_EPSILON:
                    if keep.trigger_price is None:
                        self._log.error(
                            "#651: %s %s %s leg %s has qty=%s != side %s but no "
                            "trigger price — not resizing",
                            symbol,
                            side,
                            kind,
                            keep.algo_id,
                            keep.quantity,
                            target,
                        )
                        continue
                    actions.append(await self._resize(symbol, side, keep, target))
            elif legacy and explicit_mode and self._migrate_legacy:
                actions.append(await self._migrate(symbol, side, key, legacy, target))
        return {"status": "synced", "position_amt": amt, "actions": actions}

    async def _handle_flat(
        self,
        symbol: str,
        side: str,
        managed: list[ProtectiveLeg],
        open_ids: set[str],
        actions: list[str],
    ) -> dict[str, Any]:
        key = f"{symbol}_{side}"
        if not managed:
            self._flat_first_seen.pop((symbol, side), None)
            return {"status": "flat", "actions": actions}
        now_mono = self._clock()
        now_ms = self._wall_clock() * 1000.0
        first_flat = self._flat_first_seen.setdefault((symbol, side), now_mono)
        gone: set[str] = set()
        young = False
        for leg in managed:
            if leg.create_time_ms:
                age = (now_ms - leg.create_time_ms) / 1000.0
            else:
                age = now_mono - first_flat
            if age < self._flat_grace:
                young = True
                continue
            outcome = await self._cancel_leg(symbol, leg, "cancel_flat")
            if outcome != "failed":
                gone.add(leg.algo_id)
                actions.append(f"cancel_flat:{leg.algo_id}")
        self._mark_pairs_cancelled(
            key, gone | (self._tracked_ids(key) - open_ids), "side_flat"
        )
        if young:
            self.request_sync(
                symbol, side, reason="flat_grace", delays=(self._flat_grace + 1.0,)
            )
        else:
            self._flat_first_seen.pop((symbol, side), None)
        return {"status": "flat", "actions": actions, "deferred_young_legs": young}

    async def _resize(
        self, symbol: str, side: str, leg: ProtectiveLeg, target: float
    ) -> str:
        """Cancel ``leg`` then re-place it at the side quantity (same trigger)."""
        self._log.warning(
            "#651: resizing %s %s %s leg %s from %s to side quantity %s "
            "(trigger %s, kept as is)",
            symbol,
            side,
            leg.kind,
            leg.algo_id,
            leg.quantity,
            target,
            leg.trigger_price,
        )
        outcome = await self._cancel_leg(symbol, leg, "resize_cancel")
        if outcome == "failed":
            self._record("resize", "failed")
            return f"resize_failed_cancel:{leg.algo_id}"
        if outcome == "gone":
            # The leg vanished under us — it may have just triggered. Never
            # re-place blindly; re-evaluate from fresh truth shortly.
            self.request_sync(symbol, side, reason="resize_leg_gone", delays=(3.0,))
            return f"resize_aborted_leg_gone:{leg.algo_id}"
        # Re-read the side: it may have changed while we were cancelling.
        amt = await self._read_side_amt(symbol, side)
        if amt is not None:
            if is_inverted(side, amt) or abs(amt) <= _QTY_EPSILON:
                self.request_sync(
                    symbol, side, reason="resize_side_changed", delays=(0.0,)
                )
                return f"resize_skipped_side_changed:{leg.algo_id}"
            target = self._target_quantity(symbol, abs(amt))
        try:
            new_id = await self._place_like(symbol, leg, target)
        except Exception as exc:
            self._record("resize", "failed")
            self._log.critical(
                "🚨 #651 RESIZE FAILED: cancelled %s leg %s on %s %s but could not "
                "re-place it at qty=%s trigger=%s: %s — the side has NO %s leg "
                "until the next sync/remediation",
                leg.kind,
                leg.algo_id,
                symbol,
                side,
                target,
                leg.trigger_price,
                exc,
                leg.kind,
            )
            await self._alert(
                f"protective_leg_resize_failed.{symbol}",
                {
                    "symbol": symbol,
                    "position_side": side,
                    "kind": leg.kind,
                    "cancelled_algo_id": leg.algo_id,
                    "target_quantity": target,
                    "trigger_price": leg.trigger_price,
                    "error": str(exc),
                },
            )
            self.request_sync(symbol, side, reason="resize_retry", delays=(10.0,))
            return f"resize_failed_place:{leg.algo_id}"
        await self._replace_tracked_id(symbol, side, leg.kind, leg.algo_id, new_id)
        self._record("resize", "success")
        # #738: an add-on entry grows the side; the existing leg price must not move because of it.
        self._log.info(
            "#738: %s %s %s leg trigger old=%s new=%s (qty %s -> %s)",
            symbol,
            side,
            leg.kind,
            leg.trigger_price,
            leg.trigger_price,
            leg.quantity,
            target,
        )
        return f"resize:{leg.algo_id}->{new_id}@{target}"

    async def _migrate(
        self,
        symbol: str,
        side: str,
        key: str,
        legacy: list[ProtectiveLeg],
        target: float,
    ) -> str:
        """Replace legacy closePosition legs of one kind by an explicit leg.

        Place-first: if the replacement cannot be placed, the legacy leg keeps
        protecting the side.
        """
        source = self._choose_keep(legacy)
        failed_at = self._migrate_failed_at.get(source.algo_id)
        if failed_at is not None and self._clock() - failed_at < _MIGRATE_RETRY_SEC:
            return f"migrate_backoff:{source.algo_id}"
        try:
            new_id = await self._place_like(symbol, source, target)
        except Exception as exc:
            self._migrate_failed_at[source.algo_id] = self._clock()
            self._record("migrate", "failed")
            self._log.error(
                "#651: could not migrate closePosition %s leg %s on %s %s to an "
                "explicit-quantity leg (kept the legacy leg): %s",
                source.kind,
                source.algo_id,
                symbol,
                side,
                exc,
            )
            return f"migrate_failed:{source.algo_id}"
        for old in legacy:
            await self._cancel_leg(symbol, old, "migrate_cancel_legacy")
            await self._replace_tracked_id(symbol, side, old.kind, old.algo_id, new_id)
        self._record("migrate", "success")
        self._log.warning(
            "#651: migrated %s %s %s leg %s (closePosition) -> %s (qty=%s)",
            symbol,
            side,
            source.kind,
            source.algo_id,
            new_id,
            target,
        )
        return f"migrate:{source.algo_id}->{new_id}@{target}"
