import logging

import pytest


@pytest.mark.asyncio
async def test_config_rate_limiter_is_disabled_for_data_manager_client(caplog):
    import tradeengine.api as api_module

    with caplog.at_level(logging.WARNING, logger="tradeengine.api"):
        limiter = api_module._build_config_rate_limiter()

        result = await limiter.check_rate_limit("test-agent", "/api/v1/config/trading")
        await limiter.record_change("test-agent", "/api/v1/config/trading")

    assert limiter.enabled is False
    assert result == {"allowed": True, "reason": "disabled", "quota_remaining": 999}
    assert "data-manager API client is not a Mongo client" in caplog.text
