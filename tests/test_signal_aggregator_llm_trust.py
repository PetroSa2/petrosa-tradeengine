import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from contracts.signal import Signal, StrategyMode
from tradeengine.signal_aggregator import LLMProcessor


@pytest.mark.asyncio
async def test_llm_processor_trusts_cio_reasoning():
    """
    Verifies that LLMProcessor trusts existing reasoning from petrosa-cio
    and does NOT call _get_llm_reasoning.
    """
    processor = LLMProcessor()

    # Create a signal from petrosa-cio with existing reasoning
    signal = Signal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.9,
        price=50000.0,
        quantity=1.0,
        current_price=50000.0,
        source="petrosa-cio",
        strategy="test_strat",
        strategy_mode=StrategyMode.LLM_REASONING,
        llm_reasoning="CIO TRUSTED REASONING",
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
    )

    # Mock _get_llm_reasoning to raise an error if called
    with patch.object(
        processor, "_get_llm_reasoning", side_effect=Exception("Should not be called!")
    ):
        result = await processor.process(signal, {})

        assert result["status"] == "executed"
        assert result["reason"] == "CIO audit trusted"
        assert result["llm_reasoning"]["reasoning"] == "CIO TRUSTED REASONING"
        assert result["order_params"]["stop_loss_pct"] == "0.02"
        assert result["order_params"]["take_profit_pct"] == "0.05"


@pytest.mark.asyncio
async def test_llm_processor_includes_risk_params_zero_value():
    """
    Verifies that LLMProcessor includes SL/TP even if they are 0.0.
    """
    processor = LLMProcessor()

    signal = Signal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.9,
        price=50000.0,
        quantity=1.0,
        current_price=50000.0,
        source="petrosa-cio",
        strategy="test_strat",
        strategy_mode=StrategyMode.LLM_REASONING,
        llm_reasoning="TEST",
        stop_loss_pct=0.0,  # Explicit 0.0
        take_profit_pct=0.0,  # Explicit 0.0
    )

    result = await processor.process(signal, {})
    assert result["order_params"]["stop_loss_pct"] == "0.0"
    assert result["order_params"]["take_profit_pct"] == "0.0"
