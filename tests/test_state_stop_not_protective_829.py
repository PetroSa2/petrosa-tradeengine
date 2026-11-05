"""/state answers an invalid signal stop with 422, internal failures with 500 (PetroSa2/petrosa-tradeengine#829).

Calls the handler directly so it runs in CI (TestClient-based state tests are skipped there).
"""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_state_answers_422_for_a_stop_on_the_wrong_side():
    """te#829: an invalid signal stop is a 422 the CIO can act on, not a 500 "outage"."""
    from fastapi import HTTPException

    from tradeengine import api
    from tradeengine.stop_floor import StopNotProtectiveError

    state = {"risk_limits": {}}
    with (
        patch.object(api.dispatcher, "get_cio_state", return_value=state),
        patch.object(
            api.stop_floor,
            "resolve_stop",
            side_effect=StopNotProtectiveError("LONG", 0.18208, 0.1819),
        ),
    ):
        with pytest.raises(HTTPException) as raised:
            await api.get_state(
                symbol="ADAUSDT",
                side="LONG",
                stop_contract_version=1,
                stop_loss=0.18208,
                stop_loss_pct=None,
                reference_price=0.1819,
                timeframe="30m",
                strategy_id="mean_reversion_scalper",
            )
    assert raised.value.status_code == 422
    assert raised.value.detail["error"] == "stop_not_protective"
    assert raised.value.detail["side"] == "LONG"
    assert raised.value.detail["stop_loss"] == 0.18208
    assert raised.value.detail["reference_price"] == 0.1819


@pytest.mark.asyncio
async def test_state_still_answers_500_for_an_internal_failure():
    from fastapi import HTTPException

    from tradeengine import api

    with patch.object(
        api.dispatcher, "get_cio_state", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(HTTPException) as raised:
            await api.get_state(
                symbol="BTCUSDT",
                side=None,
                stop_contract_version=None,
                stop_loss=None,
                stop_loss_pct=None,
                reference_price=None,
                timeframe=None,
                strategy_id=None,
            )
    assert raised.value.status_code == 500
