"""Small facade containing the probe's only allowed client methods."""

from __future__ import annotations

from collections.abc import Mapping

from ._client import ProbeBinanceClient


class ProbeFacade:
    def __init__(self, client: ProbeBinanceClient) -> None:
        self._client = client

    def futures_time(self):
        return self._client.futures_time()

    def futures_exchange_info(self):
        return self._client.futures_exchange_info()

    def futures_get_position_mode(self):
        return self._client.futures_get_position_mode()

    def futures_account(self):
        return self._client.futures_account()

    def futures_symbol_ticker(self, **kwargs):
        return self._client.futures_symbol_ticker(**kwargs)

    def futures_mark_price(self, **kwargs):
        return self._client.futures_mark_price(**kwargs)

    def futures_klines(self, **kwargs):
        return self._client.futures_klines(**kwargs)

    def futures_create_test_order(self, **kwargs):
        return self._client.futures_create_test_order(**kwargs)

    def recv_window_seconds(self) -> float | None:
        """The client's receive window, in seconds."""
        milliseconds = getattr(self._client, "REQUEST_RECVWINDOW", None)
        return float(milliseconds) / 1000.0 if milliseconds else None

    def response_headers(self) -> Mapping[str, str]:
        """The headers of the client's last response, names lower-cased (used weight, order count)."""
        response = getattr(self._client, "response", None)
        headers = getattr(response, "headers", None) or {}
        return {str(name).lower(): str(value) for name, value in headers.items()}
