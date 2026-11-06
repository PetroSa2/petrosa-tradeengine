"""The llm_reasoning processor has no hidden gate (petrosa-tradeengine#828)."""

from __future__ import annotations

import inspect

import pytest
from petrosa_contracts import Signal, StrategyMode

from tradeengine import signal_aggregator
from tradeengine.signal_aggregator import LLMProcessor


def _signal(source: str, reasoning: str | None, confidence: float, strategy="s1"):
    return Signal(
        strategy_id=strategy,
        symbol="BTCUSDT",
        action="buy",
        confidence=confidence,
        price=50000.0,
        quantity=1.0,
        current_price=50000.0,
        source=source,
        strategy=strategy,
        strategy_mode=StrategyMode.LLM_REASONING,
        llm_reasoning=reasoning,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [0.95, 0.71, 0.5])
async def test_non_cio_llm_reasoning_signal_is_rejected_explicitly(confidence) -> None:
    """Rejected whatever its confidence: the old 0.7 placeholder is gone."""
    result = await LLMProcessor().process(
        _signal("realtime-strategies", None, confidence), {}
    )
    assert result["status"] == "rejected"
    assert result["rejection_source"] == "llm_reasoning_missing"
    assert "realtime-strategies" in result["reason"]


@pytest.mark.asyncio
async def test_cio_signal_without_reasoning_is_rejected_explicitly() -> None:
    result = await LLMProcessor().process(_signal("petrosa-cio", "", 0.9), {})
    assert result["rejection_source"] == "llm_reasoning_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [0.68, 0.95])
async def test_cio_signal_with_reasoning_executes_at_any_confidence(confidence) -> None:
    """te#807's dropped execute had confidence 0.6797: CIO's decision stands."""
    result = await LLMProcessor().process(
        _signal("petrosa-cio", "routed: execute", confidence), {}
    )
    assert result["status"] == "executed"
    assert result["reason"] == "CIO audit trusted"
    assert result["llm_reasoning"]["reasoning"] == "routed: execute"


@pytest.mark.asyncio
async def test_no_rejection_from_the_processor_lacks_a_source(monkeypatch) -> None:
    processor = LLMProcessor()
    rejections = [
        await processor.process(_signal("other", None, 0.99), {}),
        await processor.process(_signal("petrosa-cio", None, 0.99), {}),
    ]
    processor.llm_available = False
    rejections.append(await processor.process(_signal("petrosa-cio", "r", 0.9), {}))
    processor.llm_available = True
    processor.same_direction_resolution = "reject_duplicates"
    existing = _signal("petrosa-cio", "r", 0.9, strategy="other_strategy")
    rejections.append(
        await processor.process(
            _signal("petrosa-cio", "r", 0.9), {"other_strategy": existing}
        )
    )
    assert all(r["status"] == "rejected" for r in rejections)
    assert [r["rejection_source"] for r in rejections] == [
        "llm_reasoning_missing",
        "llm_reasoning_missing",
        "llm_unavailable",
        "same_direction_conflict",
    ]


def test_the_placeholder_and_its_threshold_are_gone() -> None:
    assert not hasattr(LLMProcessor, "_get_llm_reasoning")
    source = inspect.getsource(signal_aggregator.LLMProcessor)
    assert "> 0.7" not in source and "Simulate LLM" not in source
