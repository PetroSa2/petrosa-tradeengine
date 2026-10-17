"""Run the tradeengine consumer path against the pinned contract."""

from __future__ import annotations

import importlib.metadata

import pytest
from pydantic import ValidationError

from tradeengine.consumer import Signal as ConsumerSignal

CONTRACT_VERSION = "0.1.0"


def _payload() -> dict[str, object]:
    return {
        "strategy_id": "contract-test-strategy",
        "symbol": "BTCUSDT",
        "action": "buy",
        "confidence": 0.75,
        "price": 50_000.0,
        "quantity": 0.002,
        "current_price": 50_000.0,
        "source": "petrosa-cio",
        "strategy": "contract-test-strategy",
    }


def test_real_consumer_path_accepts_pinned_contract_payload():
    assert importlib.metadata.version("petrosa-contracts") == CONTRACT_VERSION
    signal = ConsumerSignal.model_validate(_payload())
    assert signal.symbol == "BTCUSDT"


def test_producer_only_field_is_rejected_as_extra_forbidden():
    with pytest.raises(ValidationError) as raised:
        ConsumerSignal.model_validate({**_payload(), "producer_only_regression": True})

    assert any(error["type"] == "extra_forbidden" for error in raised.value.errors())


def test_missing_consumer_required_field_is_rejected():
    payload = _payload()
    payload.pop("symbol")
    with pytest.raises(ValidationError) as raised:
        ConsumerSignal.model_validate(payload)

    assert any(error["type"] == "missing" for error in raised.value.errors())
