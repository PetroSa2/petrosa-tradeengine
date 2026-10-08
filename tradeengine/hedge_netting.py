"""Policy decisions for opposite hedge-mode signals."""

from dataclasses import dataclass
from typing import Any, Literal

HedgeNettingPolicy = Literal["allow_both", "net", "block_opposite"]


@dataclass(frozen=True)
class HedgeNettingDecision:
    policy: HedgeNettingPolicy
    position_side: str | None = None
    owner_strategy_id: str | None = None
    owner_position_id: str | None = None
    quantity: float = 0.0
    skipped_quantity: float = 0.0


def normalise_policy(value: Any) -> HedgeNettingPolicy:
    return value if value in {"allow_both", "net", "block_opposite"} else "allow_both"


def opposite_side(action: str) -> str:
    return "SHORT" if action == "buy" else "LONG"


def decide(
    *,
    policy: Any,
    symbol: str,
    action: str,
    quantity: float,
    positions: list[dict[str, Any]],
) -> HedgeNettingDecision:
    resolved = normalise_policy(policy)
    if resolved == "allow_both":
        return HedgeNettingDecision(resolved)
    target_side = opposite_side(action)
    candidates = [
        position
        for position in positions
        if position.get("symbol") == symbol
        and str(position.get("side", "")).upper() == target_side
        and position.get("status") in {"open", "partially_closed"}
    ]
    if not candidates:
        return HedgeNettingDecision(resolved)
    owner = max(
        candidates, key=lambda position: abs(float(position.get("entry_quantity", 0)))
    )
    available = abs(float(owner.get("entry_quantity", owner.get("quantity", 0))))
    requested = max(float(quantity), 0.0)
    if resolved == "block_opposite":
        return HedgeNettingDecision(resolved, position_side=target_side)
    return HedgeNettingDecision(
        resolved,
        position_side=target_side,
        owner_strategy_id=str(owner.get("strategy_id") or "unknown"),
        owner_position_id=owner.get("strategy_position_id"),
        quantity=min(requested, available),
        skipped_quantity=max(requested - available, 0.0),
    )


def offset_notional(positions: list[dict[str, Any]], symbol: str) -> float:
    by_side: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}
    for position in positions:
        if position.get("symbol") != symbol or position.get("status") not in {
            "open",
            "partially_closed",
        }:
            continue
        side = str(position.get("side", "")).upper()
        quantity = abs(
            float(position.get("entry_quantity", position.get("quantity", 0)))
        )
        price = abs(float(position.get("mark_price", position.get("entry_price", 0))))
        if side in by_side:
            by_side[side] += quantity * price
    return min(by_side["LONG"], by_side["SHORT"])
