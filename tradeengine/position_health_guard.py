import logging
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from contracts.order import TradeOrder
from shared.constants import UTC

logger = logging.getLogger(__name__)


# AC3 of #424: a Binance futures algo-order ID is a 13+ digit integer.
# Anything else (price strings, sentinel placeholders, UUIDs) MUST be
# rejected at the storage boundary so stops-health verification works.
_BINANCE_ALGO_ID_RE = re.compile(r"^\d{13,}$")


def _is_real_algo_id(value: Any) -> bool:
    """True when value is a string that looks like a Binance algo order ID."""
    if value is None:
        return False
    return bool(_BINANCE_ALGO_ID_RE.match(str(value)))


class RiskOrderIds(BaseModel):
    """Storage-side model for the SL/TP Binance algo-order IDs.

    AC3 of #424: writers MUST go through this model so price-shaped
    values (e.g. "2022.6338") cannot leak into the sl_order_id /
    tp_order_id slots and pretend to be real algo orders.
    """

    sl_order_id: str | None = None
    tp_order_id: str | None = None

    @field_validator("sl_order_id", "tp_order_id")
    @classmethod
    def _must_be_binance_algo_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v)
        if not _BINANCE_ALGO_ID_RE.match(s):
            raise ValueError(
                f"order_id must be a Binance algo-order ID (>=13 digits); got {s!r}"
            )
        return s


class PositionStopStatus(BaseModel):
    strategy_position_id: str
    symbol: str
    side: str
    has_sl_order: bool
    has_tp_order: bool
    sl_order_id: str | None
    tp_order_id: str | None
    status: Literal["healthy", "missing_sl", "missing_tp", "missing_both"]
    remediation_outcome: Literal[
        "none",
        "sl_placed",
        "tp_placed",
        "both_placed",
        "sl_failed",
        "tp_failed",
        "both_failed",
        "position_closed",
        "close_failed",
    ]
    source: Literal["memory", "mysql", "both"]


class StopsDivergence(BaseModel):
    """A position whose stored order IDs are not present on Binance.

    Emitted for AC3 of #424 so the operator dashboard can list exactly
    which positions diverge from the exchange — instead of a single
    aggregate "violation_count" that hides the why.
    """

    strategy_position_id: str
    symbol: str
    side: str
    stored_sl_order_id: str | None
    stored_tp_order_id: str | None
    sl_present_on_binance: bool
    tp_present_on_binance: bool
    sl_id_is_real_algo_id: bool
    tp_id_is_real_algo_id: bool
    detail: str


class PositionStopsHealthResponse(BaseModel):
    timestamp: str
    total_checked: int
    healthy_count: int
    violation_count: int
    alarms_emitted: int
    positions: list[PositionStopStatus]
    divergences: list[StopsDivergence] = []


async def check_position_stops(
    strategy_pos_manager: Any,
    position_client: Any,
    exchange: Any,
    event_publisher: Any,
) -> PositionStopsHealthResponse:
    try:
        memory_positions: list[dict[str, Any]] = (
            strategy_pos_manager.get_all_open_strategy_positions()
        )
    except Exception as exc:
        logger.error("Failed to get in-memory positions: %s", exc)
        memory_positions = []

    try:
        mysql_positions: list[
            dict[str, Any]
        ] = await position_client.get_open_positions()
    except Exception as exc:
        logger.error("Failed to get MySQL positions: %s", exc)
        mysql_positions = []

    merged: dict[str, dict[str, Any]] = {}
    source_map: dict[str, str] = {}

    for pos in memory_positions:
        pid = pos.get("strategy_position_id")
        if not pid:
            continue
        merged[pid] = dict(pos)
        source_map[pid] = "memory"

    for pos in mysql_positions:
        pid = pos.get("strategy_position_id")
        if not pid:
            continue
        if pid in merged:
            for k, v in pos.items():
                if merged[pid].get(k) is None and v is not None:
                    merged[pid][k] = v
            source_map[pid] = "both"
        else:
            merged[pid] = dict(pos)
            source_map[pid] = "mysql"

    result_positions: list[PositionStopStatus] = []
    divergences: list[StopsDivergence] = []
    healthy_count = 0
    violation_count = 0
    alarms_emitted = 0

    # AC3 of #424: per-symbol cache of Binance open algo-order IDs. None
    # signals "verification unavailable" (exchange call failed) — we then
    # only fall back to a fail-open healthy verdict when the local IDs
    # at least pass the shape check.
    binance_open_by_symbol: dict[str, set[str] | None] = {}

    async def _binance_ids_for(symbol: str) -> set[str] | None:
        if symbol in binance_open_by_symbol:
            return binance_open_by_symbol[symbol]
        if exchange is None or not hasattr(exchange, "get_all_open_orders"):
            binance_open_by_symbol[symbol] = None
            return None
        try:
            raw = await exchange.get_all_open_orders(symbol=symbol)
        except Exception as exc:
            logger.warning(
                "stops-health: get_all_open_orders(%s) failed: %s — verification unavailable",
                symbol,
                exc,
            )
            binance_open_by_symbol[symbol] = None
            return None
        if raw is None:
            normalized: set[str] | None = set()
        else:
            normalized = {str(x) for x in raw}
        binance_open_by_symbol[symbol] = normalized
        return normalized

    for spid, pos in merged.items():
        sl_order_id = pos.get("sl_order_id")
        tp_order_id = pos.get("tp_order_id")
        has_sl = sl_order_id is not None
        has_tp = tp_order_id is not None
        src = source_map.get(spid, "memory")
        symbol_for_pos = pos.get("symbol", "unknown")

        if has_sl and has_tp:
            # AC3 of #424: verify on Binance before declaring healthy.
            # A position is healthy only when BOTH stored IDs look like
            # Binance algo IDs AND are actually open on the exchange.
            binance_ids = await _binance_ids_for(symbol_for_pos)
            sl_real = _is_real_algo_id(sl_order_id)
            tp_real = _is_real_algo_id(tp_order_id)
            sl_present = (
                binance_ids is not None and sl_real and str(sl_order_id) in binance_ids
            )
            tp_present = (
                binance_ids is not None and tp_real and str(tp_order_id) in binance_ids
            )

            verified_healthy = binance_ids is not None and sl_present and tp_present
            # Fail-open path: if Binance verification is unavailable, only
            # accept the position as healthy when local IDs at least pass
            # the shape check — otherwise the price-string bug stays hidden.
            unverified_healthy = binance_ids is None and sl_real and tp_real

            if verified_healthy or unverified_healthy:
                result_positions.append(
                    PositionStopStatus(
                        strategy_position_id=spid,
                        symbol=symbol_for_pos,
                        side=pos.get("side", "unknown"),
                        has_sl_order=True,
                        has_tp_order=True,
                        sl_order_id=str(sl_order_id),
                        tp_order_id=str(tp_order_id),
                        status="healthy",
                        remediation_outcome="none",
                        source=src,
                    )
                )
                healthy_count += 1
                continue

            # Verification ran AND the stored IDs are not both present on
            # Binance — record a divergence and fall through to remediation.
            divergences.append(
                StopsDivergence(
                    strategy_position_id=spid,
                    symbol=symbol_for_pos,
                    side=pos.get("side", "unknown"),
                    stored_sl_order_id=(
                        str(sl_order_id) if sl_order_id is not None else None
                    ),
                    stored_tp_order_id=(
                        str(tp_order_id) if tp_order_id is not None else None
                    ),
                    sl_present_on_binance=bool(sl_present),
                    tp_present_on_binance=bool(tp_present),
                    sl_id_is_real_algo_id=sl_real,
                    tp_id_is_real_algo_id=tp_real,
                    detail=(
                        f"sl_present={sl_present} tp_present={tp_present} "
                        f"sl_real={sl_real} tp_real={tp_real}"
                    ),
                )
            )
            # Rewrite local flags so the existing missing-* remediation
            # block downstream treats the unverified side as missing.
            has_sl = bool(sl_present)
            has_tp = bool(tp_present)
            if has_sl and has_tp:
                # Defensive: should be unreachable given verified_healthy
                # would have caught this; included for clarity.
                continue

        violation_count += 1
        if not has_sl and not has_tp:
            pstatus = "missing_both"
        elif not has_sl:
            pstatus = "missing_sl"
        else:
            pstatus = "missing_tp"

        symbol = pos.get("symbol", "unknown")
        position_side = pos.get("side", "LONG")
        order_side = "sell" if position_side == "LONG" else "buy"
        entry_quantity = float(pos.get("entry_quantity") or 0.0)
        stop_loss_price = pos.get("stop_loss_price")
        take_profit_price = pos.get("take_profit_price")
        strategy_id = pos.get("strategy_id", "unknown")
        avg_price = float(pos.get("avg_price") or pos.get("entry_price") or 0.0)

        originally_missing_sl = not has_sl
        originally_missing_tp = not has_tp
        close_needed = False
        sl_placed_now = False
        tp_placed_now = False

        if not has_sl:
            if stop_loss_price is not None:
                try:
                    await exchange.execute(
                        TradeOrder(
                            type="stop",
                            symbol=symbol,
                            side=order_side,
                            amount=entry_quantity,
                            stop_loss=float(stop_loss_price),
                            position_side=position_side,
                        )
                    )
                    sl_placed_now = True
                except Exception as exc:
                    logger.error("SL placement failed for %s: %s", spid, exc)
                    close_needed = True
            else:
                close_needed = True

        if not close_needed and not has_tp:
            if take_profit_price is not None:
                try:
                    await exchange.execute(
                        TradeOrder(
                            type="take_profit",
                            symbol=symbol,
                            side=order_side,
                            amount=entry_quantity,
                            take_profit=float(take_profit_price),
                            position_side=position_side,
                        )
                    )
                    tp_placed_now = True
                except Exception as exc:
                    logger.error("TP placement failed for %s: %s", spid, exc)
                    close_needed = True
            else:
                close_needed = True

        if close_needed:
            outcome = "position_closed"
            try:
                await strategy_pos_manager.close_strategy_position(
                    strategy_position_id=spid,
                    exit_price=avg_price,
                    close_reason="force_closed_missing_stops",
                )
                await event_publisher.publish(
                    event_type="position_force_closed_no_stops",
                    strategy_id=strategy_id,
                    order_id="",
                    reason=f"Missing stops for {spid}",
                )
                logger.error(
                    "Position %s force-closed: missing stops (strategy_id=%s)",
                    spid,
                    strategy_id,
                )
                alarms_emitted += 1
            except Exception as exc:
                logger.error("Failed to close position %s: %s", spid, exc)
                outcome = "close_failed"
        else:
            if originally_missing_sl and originally_missing_tp:
                outcome = "both_placed"
            elif originally_missing_sl:
                outcome = "sl_placed"
            elif originally_missing_tp:
                outcome = "tp_placed"
            else:
                outcome = "none"

        result_positions.append(
            PositionStopStatus(
                strategy_position_id=spid,
                symbol=symbol,
                side=position_side,
                has_sl_order=has_sl or sl_placed_now,
                has_tp_order=has_tp or tp_placed_now,
                sl_order_id=str(sl_order_id) if sl_order_id is not None else None,
                tp_order_id=str(tp_order_id) if tp_order_id is not None else None,
                status=pstatus,
                remediation_outcome=outcome,
                source=src,
            )
        )

    return PositionStopsHealthResponse(
        timestamp=datetime.now(UTC).isoformat(),
        total_checked=len(merged),
        healthy_count=healthy_count,
        violation_count=violation_count,
        alarms_emitted=alarms_emitted,
        positions=result_positions,
        divergences=divergences,
    )
