"""Restricted Binance client used only by the exchange-edge probe."""

from __future__ import annotations

from urllib.parse import urlsplit

import requests
from binance.client import Client
from requests.adapters import HTTPAdapter

TESTNET_HOST = "testnet.binancefuture.com"
LIVE_HOST = "fapi.binance.com"
ALLOWED_REQUESTS = frozenset(
    {
        ("GET", "/fapi/v1/time"),
        ("GET", "/fapi/v1/exchangeInfo"),
        ("GET", "/fapi/v1/positionSide/dual"),
        ("GET", "/fapi/v2/account"),
        ("GET", "/fapi/v1/ticker/price"),
        ("GET", "/fapi/v1/premiumIndex"),
        ("GET", "/fapi/v1/klines"),
        ("POST", "/fapi/v1/order/test"),
    }
)


class ProbeForbidden(RuntimeError):
    """Raised when a probe operation falls outside its safety contract."""


def _host_for(testnet: bool) -> str:
    return TESTNET_HOST if testnet else LIVE_HOST


def _allowed(method: str, url: str, *, testnet: bool) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        return False
    if parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.port not in {None, 443}:
        return False
    if parsed.query and method.upper() == "POST":
        return False
    return (method.upper(), parsed.path) in ALLOWED_REQUESTS and (
        parsed.hostname.lower() == _host_for(testnet)
    )


class _WhitelistAdapter(HTTPAdapter):
    def __init__(self, *, testnet: bool) -> None:
        super().__init__()
        self._testnet = testnet

    def send(self, request, **kwargs):
        if not _allowed(request.method, request.url, testnet=self._testnet):
            raise ProbeForbidden(f"request refused: {request.method} {request.url}")
        return super().send(request, **kwargs)


class _ForbiddenWebSocket:
    def __getattr__(self, name):
        raise ProbeForbidden(f"WebSocket access refused: {name}")


class ProbeBinanceClient(Client):
    """A synchronous client whose transport can only reach approved testnet calls."""

    REQUEST_TIMEOUT = 8

    def __init__(self, *args, testnet: bool = True, **kwargs) -> None:
        if not testnet:
            raise ProbeForbidden("the probe is testnet-only")
        if "requests_params" in kwargs:
            raise ProbeForbidden("requests_params are not accepted")
        kwargs["testnet"] = True
        kwargs["ping"] = False
        super().__init__(*args, **kwargs)
        self.ws_api = _ForbiddenWebSocket()
        self.ws_future = _ForbiddenWebSocket()

    def __getattribute__(self, name):
        if (name.startswith("ws_") or name.startswith("_ws_")) and name not in {
            "ws_api",
            "ws_future",
        }:

            def forbidden(*args, **kwargs):
                raise ProbeForbidden("WebSocket access refused")

            return forbidden
        return super().__getattribute__(name)

    def _init_session(self) -> requests.Session:
        session = super()._init_session()
        session.adapters.clear()
        adapter = _WhitelistAdapter(testnet=True)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.max_redirects = 0
        session.trust_env = False
        return session

    def _get_request_kwargs(self, method, signed, force_params=False, **kwargs):
        allowed = {
            key: kwargs[key] for key in ("timeout", "params", "data") if key in kwargs
        }
        if isinstance(allowed.get("data"), dict):
            allowed["data"] = {
                key: value
                for key, value in allowed["data"].items()
                if key != "requests_params"
            }
        allowed["timeout"] = self.REQUEST_TIMEOUT
        allowed["allow_redirects"] = False
        return super()._get_request_kwargs(method, signed, force_params, **allowed)

    def _request(self, method, uri: str, signed: bool, force_params=False, **kwargs):
        if not _allowed(method, uri, testnet=True):
            raise ProbeForbidden(f"request refused: {method} {uri}")
        return super()._request(method, uri, signed, force_params, **kwargs)

    def _ws_api_request_sync(self, *args, **kwargs):
        raise ProbeForbidden("WebSocket access refused")

    def _ws_futures_api_request_sync(self, *args, **kwargs):
        raise ProbeForbidden("WebSocket access refused")

    def _ws_api_request(self, *args, **kwargs):
        raise ProbeForbidden("WebSocket access refused")

    def _ws_futures_api_request(self, *args, **kwargs):
        raise ProbeForbidden("WebSocket access refused")


__all__ = [
    "ALLOWED_REQUESTS",
    "LIVE_HOST",
    "ProbeBinanceClient",
    "ProbeForbidden",
    "TESTNET_HOST",
    "_allowed",
]
