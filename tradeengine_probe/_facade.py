"""Small facade containing the probe's only allowed client methods."""

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
