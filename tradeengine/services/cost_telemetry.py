"""Pure cost-attribution helpers for execution-event telemetry."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

slippage_bp = Histogram(
    "tradeengine_slippage_bp",
    "Execution slippage in basis points",
    ["order_role", "side"],
    buckets=[-50, -20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20, 50, 100],
)
fills_total = Counter("tradeengine_fills_total", "Fills by liquidity", ["liquidity"])
fee_bp_last = Gauge(
    "tradeengine_fee_bp_last", "Fee basis points on the latest quote-asset fill"
)
_recorded_order_ids: set[str] = set()


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def intended_price(
    order: Any, result: dict[str, Any], *, mark_price: Any = None
) -> tuple[Decimal | None, str]:
    order_type = str(getattr(order, "type", "")).lower()
    if order_type in {"stop", "stop_market", "conditional_stop"}:
        return _decimal(getattr(order, "stop_loss", None)), "stop_trigger"
    if order_type in {"take_profit", "take_profit_market", "conditional_take_profit"}:
        return _decimal(getattr(order, "take_profit", None)), "take_profit_trigger"
    if getattr(order, "reduce_only", False) and mark_price is not None:
        return _decimal(mark_price), "manual_close_mark"
    if order_type in {"limit", "stop_limit", "take_profit_limit"}:
        return _decimal(getattr(order, "target_price", None)), "limit_price"
    metadata = getattr(order, "strategy_metadata", {}) or {}
    if metadata.get("maker_intended_price") is not None:
        return _decimal(metadata["maker_intended_price"]), "maker_limit_price"
    return _decimal(
        metadata.get("signal_price") or metadata.get("current_price")
    ), "signal_price"


def build_cost_fields(
    order: Any, result: dict[str, Any], *, mark_price: Any = None
) -> dict[str, Any]:
    fill = _decimal(result.get("fill_price") or result.get("average_price"))
    qty = _decimal(
        result.get("amount") or result.get("filled") or result.get("fill_qty")
    )
    fee_present = "fees" in result or "fee" in result
    fee = _decimal(result.get("fees", result.get("fee"))) if fee_present else None
    fee_asset = result.get("fee_asset") or result.get("commission_asset")
    if fee_asset is None:
        for item in result.get("fills") or []:
            if isinstance(item, dict) and item.get("commissionAsset"):
                fee_asset = item["commissionAsset"]
                break
    symbol = str(getattr(order, "symbol", ""))
    quote = next(
        (x for x in ("USDT", "USDC", "BUSD", "FDUSD", "USD") if symbol.endswith(x)),
        symbol,
    )
    if fee is None:
        fee = Decimal("0")
        fee_status = "unknown"
    elif fee_asset and str(fee_asset).upper() != quote.upper():
        fee_status = "needs_conversion"
    else:
        fee_status = "ok"
    intended, source = intended_price(order, result, mark_price=mark_price)
    fields: dict[str, Any] = {
        "intended_price": float(intended) if intended is not None else None,
        "intended_price_source": source,
        "fill_price": float(fill) if fill is not None else None,
        "fee": float(fee),
        "fee_asset": fee_asset or quote,
        "fee_status": fee_status,
    }
    direction = (
        Decimal("1")
        if str(getattr(order, "side", "")).lower() == "buy"
        else Decimal("-1")
    )
    fields["slippage_bp"] = (
        float((fill - intended) * direction / intended * Decimal("10000"))
        if fill is not None and intended not in (None, 0)
        else None
    )
    fields["fee_bp"] = (
        float(fee / (qty * fill) * Decimal("10000"))
        if fee_status == "ok" and fill not in (None, 0) and qty not in (None, 0)
        else None
    )
    metadata = getattr(order, "strategy_metadata", {}) or {}
    latency = metadata.get("decision_latency_ms")
    if (
        latency is None
        and metadata.get("signal_timestamp")
        and result.get("order_timestamp")
    ):
        try:
            signal_dt = datetime.fromisoformat(str(metadata["signal_timestamp"]))
            order_dt = datetime.fromtimestamp(
                float(result["order_timestamp"]) / 1000, tz=signal_dt.tzinfo
            )
            latency = (order_dt - signal_dt).total_seconds() * 1000
        except (TypeError, ValueError, OverflowError):
            pass
    fields["decision_latency_ms"] = latency
    return fields


#: The cost fields a fill event carries for the slippage report of data-manager (petrosa-data-manager#535).
SLIPPAGE_FIELDS = ("intended_price", "intended_price_source", "slippage_bp", "fee_bp")


def fill_cost_fields(
    *,
    symbol: str,
    side: str,
    order_type: str,
    fill_price: Any,
    quantity: Any,
    fee: Any = None,
    fee_asset: str | None = None,
    intended_price: Any = None,
    trigger: str | None = None,
    reduce_only: bool = False,
) -> dict[str, Any]:
    """Slippage fields of a fill that did not come from an order object (the user-data stream and the OCO
    exit path, petrosa-data-manager#561).

    ``slippage_bp`` is signed, positive = adverse, against the *intended* price: for an entry the price the
    signal carried (``intended_price``; the limit price for a limit order), for a stop-loss / take-profit exit
    the trigger price of that leg (``trigger`` = ``"stop_loss"`` / ``"take_profit"`` with ``intended_price`` as
    the trigger). Without an intended price the fields are present and null (``intended_price_source`` says
    where it would have come from), so a consumer can count the fills it cannot measure.
    """
    kind = str(order_type or "").lower()
    if trigger == "stop_loss":
        kind = "stop_market"
    elif trigger == "take_profit":
        kind = "take_profit_market"
    order = SimpleNamespace(
        symbol=symbol,
        side=str(side or "").lower(),
        type=kind,
        reduce_only=reduce_only,
        stop_loss=intended_price if trigger == "stop_loss" else None,
        take_profit=intended_price if trigger == "take_profit" else None,
        target_price=intended_price if kind in {"limit", "stop_limit"} else None,
        strategy_metadata={"signal_price": intended_price},
    )
    result: dict[str, Any] = {
        "fill_price": fill_price,
        "amount": quantity,
        "fee_asset": fee_asset,
    }
    if fee is not None:
        result["fees"] = fee
    fields = build_cost_fields(order, result)
    return {name: fields.get(name) for name in SLIPPAGE_FIELDS}


def record_fill_metrics(
    order_id: str, fields: dict[str, Any], *, role: str, side: str, liquidity: str
) -> None:
    if not order_id or order_id in _recorded_order_ids:
        return
    _recorded_order_ids.add(order_id)
    if fields.get("slippage_bp") is not None:
        slippage_bp.labels(order_role=role, side=side).observe(fields["slippage_bp"])
    fills_total.labels(liquidity=liquidity).inc()
    if fields.get("fee_bp") is not None:
        fee_bp_last.set(fields["fee_bp"])
