"""Result classification for bounded exchange checks."""

from __future__ import annotations

from enum import StrEnum

from binance.exceptions import BinanceAPIException


class Result(StrEnum):
    SUCCESS = "success"
    AUTH_ERROR = "auth_error"
    CLOCK_ERROR = "clock_error"
    EXCHANGE_REJECT = "exchange_reject"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    BLOCKED = "blocked"
    UNEXPECTED = "unexpected"
    SKIPPED = "skipped"


AUTH_CODES = {-1022, -2014, -2015, -2008, -4045}
REJECT_CODES = {-1013, -1111, -4003, -4005, -4014, -4131, -4164, -2019}


def classify_error(error: BaseException) -> Result:
    code = getattr(error, "code", None)
    status = getattr(error, "status_code", None)
    if code in AUTH_CODES or status in {401, 403}:
        return Result.AUTH_ERROR
    if code == -1021:
        return Result.CLOCK_ERROR
    if code in REJECT_CODES:
        return Result.EXCHANGE_REJECT
    if code in {-1003, -1015} or status in {418, 429}:
        return Result.RATE_LIMITED
    if isinstance(error, TimeoutError):
        return Result.TIMEOUT
    if isinstance(error, BinanceAPIException):
        return Result.UNEXPECTED
    return Result.TRANSPORT_ERROR
