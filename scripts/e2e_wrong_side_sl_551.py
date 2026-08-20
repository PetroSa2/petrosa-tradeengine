#!/usr/bin/env python3
"""Live futures-testnet E2E for tradeengine#551.

Reproduces the wrong-side stop-loss immediate-trigger (-2021) failure and
proves the fix: for a SHORT whose market has crossed ABOVE entry, the naive
entry-derived / entry-floored stop sits BELOW markPrice and Binance rejects it
with -2021 ("Order would immediately trigger"), leaving the position naked. The
fix (`enforce_market_side_stop`) re-anchors the stop to the correct side of the
LIVE market so the protective STOP_MARKET is accepted.

Runs against Binance USDT-M futures TESTNET only. Requires env:
  BINANCE_API_KEY, BINANCE_API_SECRET
  BINANCE_FUTURES_URL (default https://testnet.binancefuture.com)

Exit 0 = fix verified. Non-zero = failure (E2E gate must block PR).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from tradeengine.risk.sl_tp_direction import enforce_market_side_stop

FUT = os.environ.get("BINANCE_FUTURES_URL", "https://testnet.binancefuture.com")
KEY = os.environ["BINANCE_API_KEY"]
SECRET = os.environ["BINANCE_API_SECRET"]
SYMBOL = "XLMUSDT"
FLOOR_PCT = 0.06  # matches te_min_sl_distance_pct default (6%)


def _sign(params: dict) -> str:
    q = urllib.parse.urlencode(params)
    sig = hmac.new(SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    return f"{q}&signature={sig}"


def _req(method: str, path: str, params: dict | None = None, signed: bool = False):
    params = dict(params or {})
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        qs = _sign(params)
    else:
        qs = urllib.parse.urlencode(params)
    url = f"{FUT}{path}?{qs}" if qs else f"{FUT}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("X-MBX-APIKEY", KEY)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _tick_round(price: float) -> str:
    # XLMUSDT futures tick size is 0.0001
    return f"{round(price / 0.0001) * 0.0001:.4f}"


def main() -> int:
    assert "testnet" in FUT, f"refusing to run against non-testnet URL: {FUT}"

    _, prem = _req("GET", "/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    mark = float(prem["markPrice"])
    print(f"[live] {SYMBOL} markPrice={mark:.5f}")

    # Resolve the live SHORT size so we can place a reduce-only protective
    # STOP_MARKET on the standard order endpoint (the -2021 immediate-trigger
    # path the engine hits for reduce-only legs). Fall back to a nominal qty.
    _, prisk = _req("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
    short_amt = 0.0
    for p in prisk:
        if p.get("positionSide") == "SHORT":
            short_amt = abs(float(p.get("positionAmt") or 0.0))
    qty = f"{short_amt:.0f}" if short_amt > 0 else "10"
    print(f"[live] SHORT positionAmt={short_amt} -> reduce-only qty={qty}")

    # Clean slate: cancel any pre-existing SHORT algo stops so the repro is not
    # masked by -4130 ("order already existing"). Leaves the position naked,
    # which is exactly the state #551 concerns.
    _, existing = _req(
        "GET", "/fapi/v1/openAlgoOrders", {"symbol": SYMBOL}, signed=True
    )
    erows = existing.get("orders", existing) if isinstance(existing, dict) else existing
    for o in erows or []:
        if o.get("positionSide") == "SHORT":
            aid = o.get("algoId") or o.get("orderId")
            _req(
                "DELETE",
                "/fapi/v1/algoOrder",
                {"symbol": SYMBOL, "algoId": aid},
                signed=True,
            )
            print(f"[setup] cancelled pre-existing SHORT algo order {aid}")

    # --- Reproduce the bug: a SHORT stop BELOW market must be -2021 ---------
    wrong_side_sl = _tick_round(mark * 0.98)  # 2% BELOW mark => immediate trigger

    # Mirror the engine's exact CONDITIONAL algo params (binance.py
    # _execute_stop_order): closePosition + GTE_GTC + triggerPrice + priceProtect.
    def _algo_stop(trigger: str) -> tuple[int, dict]:
        return _req(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "symbol": SYMBOL,
                "side": "BUY",
                "type": "STOP_MARKET",
                "algoType": "CONDITIONAL",
                "timeInForce": "GTE_GTC",
                "closePosition": "true",
                "triggerPrice": trigger,
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
                "positionSide": "SHORT",
            },
            signed=True,
        )

    print(
        f"[repro] placing WRONG-side SHORT STOP_MARKET triggerPrice={wrong_side_sl} "
        f"(below mark {mark:.5f}) — expect APIError -2021"
    )
    code, resp = _algo_stop(wrong_side_sl)
    if not (code >= 400 and resp.get("code") == -2021):
        print(f"[repro] FAIL: expected -2021, got HTTP {code} {resp}")
        # clean up if it unexpectedly placed
        if code < 400 and resp.get("orderId"):
            _req(
                "DELETE",
                "/fapi/v1/order",
                {"symbol": SYMBOL, "orderId": resp["orderId"]},
                signed=True,
            )
        return 1
    print(
        f"[repro] OK: Binance rejected wrong-side stop with {resp['code']} "
        f"'{resp['msg']}' (naked-position trigger reproduced)"
    )

    # --- Apply the fix: enforce_market_side_stop re-anchors above market ----
    decision = enforce_market_side_stop(
        position_side="SHORT",
        stop_price=float(wrong_side_sl),
        market_price=mark,
        min_distance_pct=FLOOR_PCT,
    )
    print(
        f"[fix]  enforce_market_side_stop -> price={decision.price} "
        f"reanchored={decision.was_reanchored} flatten={decision.should_flatten} "
        f"reason={decision.reason!r}"
    )
    assert decision.was_reanchored and not decision.should_flatten
    assert decision.price > mark, "re-anchored SHORT SL must be ABOVE mark"

    fixed_sl = _tick_round(decision.price)
    print(
        f"[fix]  placing CORRECT-side SHORT STOP_MARKET triggerPrice={fixed_sl} "
        f"(above mark {mark:.5f}) — expect acceptance"
    )
    code, resp = _algo_stop(fixed_sl)
    if code >= 400:
        print(f"[fix]  FAIL: re-anchored stop rejected HTTP {code} {resp}")
        return 1
    algo_id = resp.get("algoId") or resp.get("orderId")
    print(
        f"[fix]  OK: STOP_MARKET accepted algoId={algo_id} "
        f"status={resp.get('algoStatus') or resp.get('status')}"
    )

    # --- Assert via openAlgoOrders that the protective leg is live & above mark
    # (CONDITIONAL closePosition legs do NOT appear in /fapi/v1/openOrders — the
    #  ticket calls for openAlgoOrders specifically). -----------------------
    _, algo = _req("GET", "/fapi/v1/openAlgoOrders", {"symbol": SYMBOL}, signed=True)
    rows = algo.get("orders", algo) if isinstance(algo, dict) else algo
    protective = [
        o
        for o in rows
        if o.get("orderType", o.get("type")) == "STOP_MARKET"
        and o.get("positionSide") == "SHORT"
    ]

    def _trig(o: dict) -> float:
        return float(o.get("triggerPrice") or o.get("stopPrice") or 0.0)

    print(
        f"[verify] live SHORT STOP_MARKET algo legs: "
        f"{[(o.get('algoId', o.get('orderId')), _trig(o)) for o in protective]}"
    )
    assert any(_trig(o) > mark for o in protective), (
        "no SHORT protective stop above mark — position would be naked"
    )
    print("[verify] OK: SHORT position protected by stop ABOVE mark (not naked)")

    # --- Cleanup: cancel the test algo order (leave account as found) -------
    _req(
        "DELETE",
        "/fapi/v1/algoOrder",
        {"symbol": SYMBOL, "algoId": algo_id},
        signed=True,
    )
    print(f"[cleanup] cancelled test algoId={algo_id}")

    print(
        "\nE2E #551 PASS: wrong-side stop rejected (-2021), "
        "fix re-anchored above market and was accepted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
