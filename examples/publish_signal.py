#!/usr/bin/env python3
"""
Signal Publishing Example - Petrosa Trading Engine

This script demonstrates how to publish trading signals to the Petrosa Trading Engine
with different timeframes and conflict resolution scenarios.

Usage:
    python publish_signal.py

Features:
    - Multiple timeframe signals (1m, 5m, 1h, 4h, 1d)
    - Different strategy modes (deterministic, ml_light, llm_reasoning)
    - Conflict resolution demonstration
    - Advanced order types
    - Risk management parameters
"""

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from typing import Dict, Any

import httpx
from contracts.signal import Signal, SignalStrength, StrategyMode, TimeFrame, OrderType, TimeInForce


class SignalPublisher:
    """Publishes trading signals to the Petrosa Trading Engine"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
    
    async def publish_signal(self, signal: Signal) -> Dict[str, Any]:
        """Publish a signal to the trading engine"""
        try:
            response = await self.client.post(
                f"{self.base_url}/trade",
                json=signal.model_dump(),
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code} - {e.response.text}")
            return {"status": "error", "error": str(e)}
        except Exception as e:
            print(f"Error publishing signal: {e}")
            return {"status": "error", "error": str(e)}
    
    def create_momentum_signal(self, symbol: str, timeframe: TimeFrame, confidence: float = 0.8) -> Signal:
        """Create a momentum strategy signal"""
        current_price = 50000 + random.uniform(-1000, 1000)
        
        return Signal(
            strategy_id="momentum_strategy",
            signal_id=f"momentum_{symbol}_{timeframe.value}_{int(time.time())}",
            strategy_mode=StrategyMode.DETERMINISTIC,
            symbol=symbol,
            action="buy",
            confidence=confidence,
            strength=SignalStrength.STRONG,
            timeframe=timeframe,
            current_price=current_price,
            target_price=current_price * 1.02,  # 2% target
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.05,  # 5% of portfolio
            stop_loss=current_price * 0.98,  # 2% stop loss
            take_profit=current_price * 1.05,  # 5% take profit
            rationale=f"Momentum signal on {timeframe.value} timeframe - strong upward trend detected",
            meta={
                "strategy_type": "momentum",
                "timeframe": timeframe.value,
                "indicators": {
                    "rsi": 65,
                    "macd": "bullish",
                    "volume": "increasing"
                }
            }
        )
    
    def create_mean_reversion_signal(self, symbol: str, timeframe: TimeFrame, confidence: float = 0.7) -> Signal:
        """Create a mean reversion strategy signal"""
        current_price = 50000 + random.uniform(-1000, 1000)
        
        return Signal(
            strategy_id="mean_reversion_strategy",
            signal_id=f"meanrev_{symbol}_{timeframe.value}_{int(time.time())}",
            strategy_mode=StrategyMode.ML_LIGHT,
            symbol=symbol,
            action="sell",
            confidence=confidence,
            strength=SignalStrength.MEDIUM,
            timeframe=timeframe,
            current_price=current_price,
            target_price=current_price * 0.98,  # 2% target
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.03,  # 3% of portfolio
            stop_loss=current_price * 1.02,  # 2% stop loss
            take_profit=current_price * 0.95,  # 5% take profit
            model_confidence=confidence,
            model_features={
                "bollinger_position": 0.8,
                "rsi": 75,
                "price_deviation": 0.02
            },
            rationale=f"Mean reversion signal on {timeframe.value} timeframe - overbought conditions detected",
            meta={
                "strategy_type": "mean_reversion",
                "timeframe": timeframe.value,
                "indicators": {
                    "bollinger_upper": current_price * 1.02,
                    "bollinger_lower": current_price * 0.98,
                    "rsi": 75
                }
            }
        )
    
    def create_llm_signal(self, symbol: str, timeframe: TimeFrame, confidence: float = 0.9) -> Signal:
        """Create an LLM reasoning strategy signal"""
        current_price = 50000 + random.uniform(-1000, 1000)
        
        return Signal(
            strategy_id="llm_strategy",
            signal_id=f"llm_{symbol}_{timeframe.value}_{int(time.time())}",
            strategy_mode=StrategyMode.LLM_REASONING,
            symbol=symbol,
            action="buy",
            confidence=confidence,
            strength=SignalStrength.EXTREME,
            timeframe=timeframe,
            current_price=current_price,
            target_price=current_price * 1.03,  # 3% target
            order_type=OrderType.STOP_LIMIT,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.08,  # 8% of portfolio
            stop_loss=current_price * 0.97,  # 3% stop loss
            take_profit=current_price * 1.06,  # 6% take profit
            conditional_price=current_price * 1.01,
            conditional_direction="above",
            llm_reasoning="Based on comprehensive market analysis, including technical indicators, sentiment analysis, and macroeconomic factors, this appears to be a strong buying opportunity. The combination of oversold conditions, positive news sentiment, and institutional buying patterns suggests a significant upward move is likely.",
            llm_alternatives=[
                {"action": "hold", "confidence": 0.3, "reason": "Wait for clearer confirmation"},
                {"action": "sell", "confidence": 0.1, "reason": "Potential downside risk"}
            ],
            rationale=f"LLM reasoning signal on {timeframe.value} timeframe - comprehensive analysis indicates strong buy opportunity",
            meta={
                "strategy_type": "llm_reasoning",
                "timeframe": timeframe.value,
                "sentiment_score": 0.8,
                "news_impact": "positive",
                "institutional_flow": "buying"
            }
        )
    
    async def demonstrate_timeframe_conflicts(self):
        """Demonstrate timeframe-based conflict resolution"""
        print("\n=== Timeframe Conflict Resolution Demo ===")
        
        symbol = "BTCUSDT"
        timeframes = [TimeFrame.MINUTE_1, TimeFrame.MINUTE_5, TimeFrame.HOUR_1, TimeFrame.HOUR_4, TimeFrame.DAY_1]
        
        # Create conflicting signals with different timeframes
        signals = []
        for i, timeframe in enumerate(timeframes):
            if i % 3 == 0:
                signal = self.create_momentum_signal(symbol, timeframe, confidence=0.7 + i * 0.05)
            elif i % 3 == 1:
                signal = self.create_mean_reversion_signal(symbol, timeframe, confidence=0.6 + i * 0.05)
            else:
                signal = self.create_llm_signal(symbol, timeframe, confidence=0.8 + i * 0.05)
            
            signals.append(signal)
        
        # Publish signals in sequence to demonstrate conflict resolution
        for i, signal in enumerate(signals):
            print(f"\nPublishing signal {i+1}/{len(signals)}:")
            print(f"  Strategy: {signal.strategy_id}")
            print(f"  Action: {signal.action}")
            print(f"  Timeframe: {signal.timeframe.value}")
            print(f"  Confidence: {signal.confidence:.2f}")
            print(f"  Mode: {signal.strategy_mode.value}")
            
            result = await self.publish_signal(signal)
            print(f"  Result: {result.get('status', 'unknown')}")
            if 'result' in result and 'aggregation_result' in result['result']:
                agg_result = result['result']['aggregation_result']
                if agg_result.get('status') == 'rejected':
                    print(f"  Reason: {agg_result.get('reason', 'Unknown')}")
            
            # Small delay between signals
            await asyncio.sleep(1)
    
    async def demonstrate_strategy_modes(self):
        """Demonstrate different strategy modes"""
        print("\n=== Strategy Modes Demo ===")
        
        symbol = "ETHUSDT"
        timeframe = TimeFrame.HOUR_1
        
        # Deterministic mode
        deterministic_signal = self.create_momentum_signal(symbol, timeframe, confidence=0.8)
        print(f"\nPublishing deterministic signal:")
        print(f"  Strategy: {deterministic_signal.strategy_id}")
        print(f"  Mode: {deterministic_signal.strategy_mode.value}")
        result = await self.publish_signal(deterministic_signal)
        print(f"  Result: {result.get('status', 'unknown')}")
        
        await asyncio.sleep(1)
        
        # ML Light mode
        ml_signal = self.create_mean_reversion_signal(symbol, timeframe, confidence=0.75)
        print(f"\nPublishing ML Light signal:")
        print(f"  Strategy: {ml_signal.strategy_id}")
        print(f"  Mode: {ml_signal.strategy_mode.value}")
        result = await self.publish_signal(ml_signal)
        print(f"  Result: {result.get('status', 'unknown')}")
        
        await asyncio.sleep(1)
        
        # LLM Reasoning mode
        llm_signal = self.create_llm_signal(symbol, timeframe, confidence=0.9)
        print(f"\nPublishing LLM Reasoning signal:")
        print(f"  Strategy: {llm_signal.strategy_id}")
        print(f"  Mode: {llm_signal.strategy_mode.value}")
        result = await self.publish_signal(llm_signal)
        print(f"  Result: {result.get('status', 'unknown')}")
    
    async def demonstrate_advanced_orders(self):
        """Demonstrate advanced order types"""
        print("\n=== Advanced Order Types Demo ===")
        
        symbol = "ADAUSDT"
        timeframe = TimeFrame.MINUTE_15
        current_price = 0.5 + random.uniform(-0.05, 0.05)
        
        # Stop Limit Order
        stop_limit_signal = Signal(
            strategy_id="advanced_strategy",
            signal_id=f"stop_limit_{symbol}_{int(time.time())}",
            strategy_mode=StrategyMode.DETERMINISTIC,
            symbol=symbol,
            action="buy",
            confidence=0.85,
            strength=SignalStrength.STRONG,
            timeframe=timeframe,
            current_price=current_price,
            target_price=current_price * 1.01,
            order_type=OrderType.STOP_LIMIT,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.04,
            stop_loss=current_price * 0.98,
            take_profit=current_price * 1.04,
            conditional_price=current_price * 1.005,
            conditional_direction="above",
            rationale="Stop limit order to buy on breakout",
            meta={"order_type": "stop_limit", "breakout_strategy": True}
        )
        
        print(f"\nPublishing Stop Limit Order:")
        print(f"  Symbol: {symbol}")
        print(f"  Order Type: {stop_limit_signal.order_type.value}")
        print(f"  Conditional Price: {stop_limit_signal.conditional_price}")
        result = await self.publish_signal(stop_limit_signal)
        print(f"  Result: {result.get('status', 'unknown')}")
        
        await asyncio.sleep(1)
        
        # Take Profit Limit Order
        take_profit_signal = Signal(
            strategy_id="advanced_strategy",
            signal_id=f"take_profit_{symbol}_{int(time.time())}",
            strategy_mode=StrategyMode.DETERMINISTIC,
            symbol=symbol,
            action="sell",
            confidence=0.8,
            strength=SignalStrength.MEDIUM,
            timeframe=timeframe,
            current_price=current_price,
            target_price=current_price * 0.99,
            order_type=OrderType.TAKE_PROFIT_LIMIT,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.03,
            stop_loss=current_price * 1.02,
            take_profit=current_price * 0.96,
            conditional_price=current_price * 0.985,
            conditional_direction="below",
            rationale="Take profit limit order to sell on pullback",
            meta={"order_type": "take_profit_limit", "profit_taking": True}
        )
        
        print(f"\nPublishing Take Profit Limit Order:")
        print(f"  Symbol: {symbol}")
        print(f"  Order Type: {take_profit_signal.order_type.value}")
        print(f"  Conditional Price: {take_profit_signal.conditional_price}")
        result = await self.publish_signal(take_profit_signal)
        print(f"  Result: {result.get('status', 'unknown')}")


async def main():
    """Main function to run the signal publishing demo"""
    publisher = SignalPublisher()
    
    try:
        print("Petrosa Trading Engine - Signal Publishing Demo")
        print("=" * 50)
        
        # Check if the service is running
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:8000/health")
                if response.status_code == 200:
                    print("✓ Trading Engine is running")
                else:
                    print("✗ Trading Engine is not responding")
                    return
        except Exception as e:
            print(f"✗ Cannot connect to Trading Engine: {e}")
            return
        
        # Run demonstrations
        await publisher.demonstrate_strategy_modes()
        await publisher.demonstrate_advanced_orders()
        await publisher.demonstrate_timeframe_conflicts()
        
        print("\n" + "=" * 50)
        print("Demo completed successfully!")
        
    except Exception as e:
        print(f"Error during demo: {e}")
    finally:
        await publisher.close()


if __name__ == "__main__":
    asyncio.run(main())
