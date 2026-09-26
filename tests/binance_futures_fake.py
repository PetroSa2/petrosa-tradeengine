"""In-memory Binance USDⓈ-M futures client for protective-leg tests (#650/#651).

Stands in for ``python-binance``'s ``Client`` underneath a REAL
``BinanceFuturesExchange``, so tests exercise the actual request building
(algo-order params, quantity rounding, cancel routing) against an exchange-like
store of positions and conditional (algo) orders — the same surface the
production incidents involved:

- ``POST /fapi/v1/algoOrder`` stores a conditional order (``algoId``);
- ``DELETE /fapi/v1/algoOrder`` cancels by ``algoId`` (``-2011`` when unknown);
- ``GET /fapi/v1/openAlgoOrders`` lists them (``orderType``, ``quantity``,
  ``closePosition``, ``createTime`` — the real response shape);
- ``futures_cancel_order`` (standard ``/order``) knows nothing about algo
  orders and answers ``-2011`` for an algo id, exactly like Binance does —
  the root cause of #650.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tradeengine.exchange.binance import BinanceFuturesExchange


class FakeBinanceAPIException(Exception):
    """``BinanceAPIException`` look-alike (``code`` / ``message``, same ``str``).

    Several older test modules replace ``sys.modules["binance"]`` with a
    MagicMock at import time, so depending on xdist collection order the
    production module may have bound mocked enums and a mocked exception
    class. ``pin_binance_module`` swaps in real values for these tests.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"APIError(code={code}): {message}")
        self.code = code
        self.message = message
        self.status_code = 400


def api_error(code: int, msg: str) -> FakeBinanceAPIException:
    """A Binance API error carrying ``code``."""
    return FakeBinanceAPIException(code, msg)


_REAL_BINANCE_CONSTANTS = {
    "SIDE_BUY": "BUY",
    "SIDE_SELL": "SELL",
    "TIME_IN_FORCE_GTC": "GTC",
    "FUTURE_ORDER_TYPE_LIMIT": "LIMIT",
    "FUTURE_ORDER_TYPE_MARKET": "MARKET",
    "FUTURE_ORDER_TYPE_STOP": "STOP",
    "FUTURE_ORDER_TYPE_STOP_MARKET": "STOP_MARKET",
    "FUTURE_ORDER_TYPE_TAKE_PROFIT": "TAKE_PROFIT",
    "FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET": "TAKE_PROFIT_MARKET",
}


def pin_binance_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give ``tradeengine.exchange.binance`` the real python-binance values."""
    import tradeengine.exchange.binance as binance_module

    for name, value in _REAL_BINANCE_CONSTANTS.items():
        monkeypatch.setattr(binance_module, name, value)
    monkeypatch.setattr(binance_module, "BinanceAPIException", FakeBinanceAPIException)


SYMBOL_INFO: dict[str, Any] = {
    "BTCUSDT": {
        "status": "TRADING",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {
                "filterType": "PERCENT_PRICE",
                "multiplierUp": "1.5",
                "multiplierDown": "0.5",
            },
        ],
    },
    "DOTUSDT": {
        "status": "TRADING",
        "baseAsset": "DOT",
        "quoteAsset": "USDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {
                "filterType": "PERCENT_PRICE",
                "multiplierUp": "1.5",
                "multiplierDown": "0.5",
            },
        ],
    },
}

PRICES = {"BTCUSDT": 50000.0, "DOTUSDT": 4.0}


class FakeFuturesClient:
    """Exchange-like store behind ``BinanceFuturesExchange.client``."""

    def __init__(self) -> None:
        # (symbol, positionSide) -> signed positionAmt
        self.positions: dict[tuple[str, str], float] = {}
        # algoId (str) -> order dict in /openAlgoOrders shape
        self.algo_orders: dict[str, dict[str, Any]] = {}
        # every _request_futures_api call: (method, path, data)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.standard_cancels: list[tuple[str, Any]] = []
        self.created_orders: list[dict[str, Any]] = []
        self.position_reads = 0
        self.cancel_errors: dict[str, Exception] = {}
        self.post_errors: list[Exception] = []
        # store the order THEN raise (an ambiguous timeout that did land)
        self.post_lands_then_raises: list[Exception] = []
        self.now_ms: float | None = None
        self._next_id = 1000000218653800

    # -- helpers used by tests ---------------------------------------------
    def set_position(self, symbol: str, side: str, amt: float) -> None:
        self.positions[(symbol, side)] = amt

    def add_algo_order(self, **fields: Any) -> str:
        algo_id = str(self._next_id)
        self._next_id += 1
        order = {
            "algoId": int(algo_id),
            "clientAlgoId": f"c{algo_id}",
            "algoType": "CONDITIONAL",
            "orderType": fields.pop("orderType", "STOP_MARKET"),
            "symbol": fields.pop("symbol", "BTCUSDT"),
            "side": fields.pop("side", "SELL"),
            "positionSide": fields.pop("positionSide", "LONG"),
            "timeInForce": fields.pop("timeInForce", "GTC"),
            "quantity": fields.pop("quantity", "0"),
            "algoStatus": fields.pop("algoStatus", "NEW"),
            "triggerPrice": fields.pop("triggerPrice", "0"),
            "price": fields.pop("price", "0"),
            "workingType": "MARK_PRICE",
            "closePosition": fields.pop("closePosition", False),
            "reduceOnly": False,
            "createTime": fields.pop("createTime", self._now_ms()),
        }
        order.update(fields)
        self.algo_orders[algo_id] = order
        return algo_id

    def fill_leg(self, algo_id: str, new_position_amt: float) -> None:
        """The conditional leg triggered: it leaves the book and the side moves."""
        order = self.algo_orders.pop(str(algo_id))
        self.positions[(order["symbol"], order["positionSide"])] = new_position_amt

    def open_legs(self, symbol: str, side: str) -> list[dict[str, Any]]:
        return [
            o
            for o in self.algo_orders.values()
            if o["symbol"] == symbol and o["positionSide"] == side
        ]

    def posts(self) -> list[dict[str, Any]]:
        return [d for m, p, d in self.requests if m == "post" and p == "algoOrder"]

    def deletes(self) -> list[dict[str, Any]]:
        return [d for m, p, d in self.requests if m == "delete" and p == "algoOrder"]

    def _now_ms(self) -> float:
        return self.now_ms if self.now_ms is not None else time.time() * 1000

    # -- python-binance Client surface ---------------------------------------
    def futures_position_information(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.position_reads += 1
        symbol = kwargs.get("symbol")
        rows = []
        for (sym, side), amt in self.positions.items():
            if symbol and sym != symbol:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "positionSide": side,
                    "positionAmt": str(amt),
                    "entryPrice": str(PRICES.get(sym, 100.0)),
                    "markPrice": str(PRICES.get(sym, 100.0)),
                }
            )
        return rows

    def futures_symbol_ticker(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "price": str(PRICES.get(symbol, 100.0))}

    def futures_cancel_order(self, symbol: str, orderId: Any) -> dict[str, Any]:
        # The standard /order endpoint knows nothing about algo orders.
        self.standard_cancels.append((symbol, orderId))
        raise api_error(-2011, "Unknown order sent.")

    def futures_create_order(self, **kwargs: Any) -> dict[str, Any]:
        self.created_orders.append(kwargs)
        return {"orderId": 1, "status": "FILLED", **kwargs}

    def futures_get_open_orders(self, symbol: str | None = None) -> list[Any]:
        return []

    def futures_get_order(self, symbol: str, orderId: Any) -> dict[str, Any]:
        raise api_error(-2013, "Order does not exist.")

    def _request_futures_api(
        self,
        method: str,
        path: str,
        signed: bool = False,
        force_params: bool = False,
        **kwargs: Any,
    ) -> Any:
        data = dict(kwargs.get("data") or kwargs.get("params") or {})
        self.requests.append((method, path, data))
        if method == "post" and path == "algoOrder":
            if self.post_errors:
                raise self.post_errors.pop(0)
            fields = {
                "orderType": data["type"],
                "symbol": data["symbol"],
                "side": data["side"],
                "positionSide": data.get("positionSide", "BOTH"),
                "timeInForce": data.get("timeInForce"),
                "quantity": data.get("quantity", "0"),
                "triggerPrice": data.get("triggerPrice"),
                "price": data.get("price", "0"),
                "closePosition": data.get("closePosition") in (True, "true"),
            }
            algo_id = self.add_algo_order(**fields)
            if self.post_lands_then_raises:
                raise self.post_lands_then_raises.pop(0)
            return dict(self.algo_orders[algo_id])
        if method == "delete" and path == "algoOrder":
            algo_id = str(data["algoId"])
            if algo_id in self.cancel_errors:
                raise self.cancel_errors.pop(algo_id)
            if algo_id not in self.algo_orders:
                raise api_error(-2011, "Unknown order sent.")
            self.algo_orders.pop(algo_id)
            return {"algoId": int(algo_id), "code": "200", "msg": "success"}
        if method == "get" and path == "openAlgoOrders":
            symbol = data.get("symbol")
            return [
                dict(o)
                for o in self.algo_orders.values()
                if not symbol or o["symbol"] == symbol
            ]
        raise AssertionError(f"unexpected futures API call {method} {path}")


def make_exchange(client: FakeFuturesClient | None = None) -> BinanceFuturesExchange:
    """A real ``BinanceFuturesExchange`` wired to the fake client."""
    exchange = BinanceFuturesExchange()
    exchange.client = client or FakeFuturesClient()  # type: ignore[assignment]
    exchange.initialized = True
    exchange.symbol_info = {k: dict(v) for k, v in SYMBOL_INFO.items()}
    return exchange
