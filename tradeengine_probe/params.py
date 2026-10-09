"""Pure test-order parameter construction."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from tradeengine.minimum_order import FALLBACK_MARGIN, minimum_quantity


def _decimal_places(value: float | str) -> int:
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    return max(0, -exponent)


def _floor_step(value: float, step: float | str) -> float:
    step_d = Decimal(str(step))
    return float(
        (Decimal(str(value)) / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d
    )


def build_order_params(
    *,
    symbol: str,
    side: str,
    order_type: str,
    price: float,
    step: float | str,
    min_qty: float | str,
    min_notional: float | str,
    margin: float | None = None,
    position_side: str | None = None,
    reduce_only: bool = False,
    limit_price: float | None = None,
    client_order_id: str | None = None,
) -> dict[str, str | bool]:
    effective_margin = FALLBACK_MARGIN if margin is None else margin
    quantity = minimum_quantity(
        price=price,
        step=step,
        min_qty=min_qty,
        min_notional=min_notional,
        margin=effective_margin,
    )
    params: dict[str, str | bool] = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": f"{quantity:.{_decimal_places(step)}f}",
    }
    if order_type == "LIMIT":
        if limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        params["timeInForce"] = "GTC"
        params["price"] = f"{_floor_step(limit_price, step):.{_decimal_places(step)}f}"
    if position_side and position_side != "BOTH":
        params["positionSide"] = position_side
    elif reduce_only:
        params["reduceOnly"] = True
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    return params
