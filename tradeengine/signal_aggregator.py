"""
Signal Aggregator - Multi-strategy signal processing and conflict resolution

This module handles incoming signals from multiple strategies, resolves conflicts,
and makes intelligent execution decisions based on signal strength, confidence,
and risk management rules. Supports three modes: deterministic, ML light, and LLM reasoning.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from enum import Enum

from contracts.signal import Signal, SignalStrength, StrategyMode, TimeFrame
from shared.constants import (
    MAX_SIGNAL_AGE_SECONDS,
    SIGNAL_CONFLICT_RESOLUTION,
    TIMEFRAME_CONFLICT_RESOLUTION,
    RISK_MANAGEMENT_ENABLED,
    MAX_POSITION_SIZE_PCT,
    MAX_DAILY_LOSS_PCT,
    STRATEGY_WEIGHTS,
    TIMEFRAME_WEIGHTS,
    DETERMINISTIC_MODE_ENABLED,
    ML_LIGHT_MODE_ENABLED,
    LLM_REASONING_MODE_ENABLED,
)
from shared.audit import audit_logger

logger = logging.getLogger(__name__)


class ConflictResolutionStrategy(str, Enum):
    """Conflict resolution strategies"""
    STRONGEST_WINS = "strongest_wins"
    FIRST_COME_FIRST_SERVED = "first_come_first_served"
    MANUAL_REVIEW = "manual_review"
    WEIGHTED_AVERAGE = "weighted_average"
    HIGHER_TIMEFRAME_WINS = "higher_timeframe_wins"
    TIMEFRAME_WEIGHTED = "timeframe_weighted"


class SignalAggregator:
    """Aggregates and processes signals from multiple strategies with three modes"""
    
    def __init__(self):
        self.active_signals: Dict[str, Signal] = {}
        self.signal_history: List[Signal] = []
        self.strategy_weights: Dict[str, float] = STRATEGY_WEIGHTS.copy()
        self.portfolio_positions: Dict[str, Dict[str, Any]] = {}
        self.daily_pnl: float = 0.0
        self.max_daily_loss: float = 0.0
        
        # Mode-specific processors
        self.deterministic_processor = DeterministicProcessor()
        self.ml_processor = MLProcessor()
        self.llm_processor = LLMProcessor()
        
    async def process_signal(self, signal: Signal) -> Dict[str, Any]:
        """Process incoming signal and return execution decision"""
        try:
            logger.info(f"Processing signal from {signal.strategy_id}: {signal.action} {signal.symbol} (mode: {signal.strategy_mode})")
            await audit_logger.log_signal(signal.model_dump(), status="processing")
            
            # Validate signal
            self._validate_signal(signal)
            
            # Check signal age
            if self._is_signal_expired(signal):
                await audit_logger.log_signal(signal.model_dump(), status="expired")
                return {"status": "expired", "reason": "Signal too old"}
            
            # Check risk limits
            if not self._check_risk_limits(signal):
                await audit_logger.log_signal(signal.model_dump(), status="risk_rejected")
                return {"status": "rejected", "reason": "Risk limits exceeded"}
            
            # Process based on strategy mode
            if signal.strategy_mode == StrategyMode.DETERMINISTIC:
                result = await self._process_deterministic_signal(signal)
            elif signal.strategy_mode == StrategyMode.ML_LIGHT:
                result = await self._process_ml_signal(signal)
            elif signal.strategy_mode == StrategyMode.LLM_REASONING:
                result = await self._process_llm_signal(signal)
            else:
                result = {"status": "error", "reason": f"Unknown strategy mode: {signal.strategy_mode}"}
            await audit_logger.log_signal(signal.model_dump(), status=result.get("status", "processed"), extra={"result": result})
            return result
            
        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            await audit_logger.log_error(str(e), context={"signal": signal.model_dump()})
            return {"status": "error", "error": str(e)}
    
    async def _process_deterministic_signal(self, signal: Signal) -> Dict[str, Any]:
        """Process signal using deterministic rule-based logic"""
        if not DETERMINISTIC_MODE_ENABLED:
            return {"status": "rejected", "reason": "Deterministic mode disabled"}
        
        # Resolve conflicts with existing signals
        conflict_result = self._resolve_conflicts(signal)
        if conflict_result["has_conflict"]:
            return self._handle_conflict(signal, conflict_result)
        
        # Use deterministic processor
        decision = await self.deterministic_processor.process(signal, self.active_signals)
        
        # Store signal if approved
        if decision["status"] == "executed":
            self._store_signal(signal)
        
        return decision
    
    async def _process_ml_signal(self, signal: Signal) -> Dict[str, Any]:
        """Process signal using light ML models"""
        if not ML_LIGHT_MODE_ENABLED:
            return {"status": "rejected", "reason": "ML mode disabled"}
        
        # Use ML processor
        decision = await self.ml_processor.process(signal, self.active_signals)
        
        # Store signal if approved
        if decision["status"] == "executed":
            self._store_signal(signal)
        
        return decision
    
    async def _process_llm_signal(self, signal: Signal) -> Dict[str, Any]:
        """Process signal using LLM reasoning"""
        if not LLM_REASONING_MODE_ENABLED:
            return {"status": "rejected", "reason": "LLM mode disabled"}
        
        # Use LLM processor
        decision = await self.llm_processor.process(signal, self.active_signals)
        
        # Store signal if approved
        if decision["status"] == "executed":
            self._store_signal(signal)
        
        return decision
    
    def _validate_signal(self, signal: Signal) -> None:
        """Validate incoming signal"""
        if signal.confidence < 0 or signal.confidence > 1:
            raise ValueError("Signal confidence must be between 0 and 1")
        
        if signal.position_size_pct and (signal.position_size_pct < 0 or signal.position_size_pct > 1):
            raise ValueError("Position size percentage must be between 0 and 1")
        
        if signal.stop_loss_pct and (signal.stop_loss_pct < 0 or signal.stop_loss_pct > 1):
            raise ValueError("Stop loss percentage must be between 0 and 1")
        
        if signal.model_confidence and (signal.model_confidence < 0 or signal.model_confidence > 1):
            raise ValueError("Model confidence must be between 0 and 1")
    
    def _is_signal_expired(self, signal: Signal) -> bool:
        """Check if signal is too old"""
        max_age = timedelta(seconds=MAX_SIGNAL_AGE_SECONDS)
        return datetime.utcnow() - signal.timestamp > max_age
    
    def _check_risk_limits(self, signal: Signal) -> bool:
        """Check risk management limits"""
        if not RISK_MANAGEMENT_ENABLED:
            return True
        
        # Check daily loss limit
        if self.daily_pnl < -self.max_daily_loss:
            logger.warning("Daily loss limit exceeded")
            return False
        
        # Check position size limit
        if signal.position_size_pct and signal.position_size_pct > MAX_POSITION_SIZE_PCT:
            logger.warning("Position size limit exceeded")
            return False
        
        return True
    
    def _resolve_conflicts(self, new_signal: Signal) -> Dict[str, Any]:
        """Resolve conflicts with existing signals for the same symbol"""
        symbol = new_signal.symbol
        existing_signals = [s for s in self.active_signals.values() if s.symbol == symbol]
        
        if not existing_signals:
            return {"has_conflict": False}
        
        # Check for opposing actions
        opposing_signals = [
            s for s in existing_signals 
            if s.action != new_signal.action and s.action in ["buy", "sell"]
        ]
        
        if not opposing_signals:
            return {"has_conflict": False}
        
        # Calculate signal strength comparison
        new_strength = self._calculate_signal_strength(new_signal)
        max_existing_strength = max(
            self._calculate_signal_strength(s) for s in opposing_signals
        )
        
        # Calculate timeframe-based strength
        new_timeframe_strength = self._calculate_timeframe_strength(new_signal)
        max_existing_timeframe_strength = max(
            self._calculate_timeframe_strength(s) for s in opposing_signals
        )
        
        return {
            "has_conflict": True,
            "new_strength": new_strength,
            "existing_strength": max_existing_strength,
            "new_timeframe_strength": new_timeframe_strength,
            "existing_timeframe_strength": max_existing_timeframe_strength,
            "opposing_signals": opposing_signals
        }
    
    def _calculate_signal_strength(self, signal: Signal) -> float:
        """Calculate signal strength based on confidence, strategy weight, and indicators"""
        base_strength = signal.confidence
        
        # Apply strategy weight
        strategy_weight = self.strategy_weights.get(signal.strategy_id, 1.0)
        weighted_strength = base_strength * strategy_weight
        
        # Apply strength multiplier
        strength_multipliers = {
            SignalStrength.WEAK: 0.5,
            SignalStrength.MEDIUM: 1.0,
            SignalStrength.STRONG: 1.5,
            SignalStrength.EXTREME: 2.0
        }
        multiplier = strength_multipliers.get(signal.strength, 1.0)
        
        # Apply mode-specific adjustments
        mode_multipliers = {
            StrategyMode.DETERMINISTIC: 1.0,
            StrategyMode.ML_LIGHT: 1.2,
            StrategyMode.LLM_REASONING: 1.5
        }
        mode_multiplier = mode_multipliers.get(signal.strategy_mode, 1.0)
        
        return weighted_strength * multiplier * mode_multiplier
    
    def _calculate_timeframe_strength(self, signal: Signal) -> float:
        """Calculate timeframe-based signal strength"""
        # Get timeframe weight
        timeframe_weight = TIMEFRAME_WEIGHTS.get(signal.timeframe.value, 1.0)
        
        # Base strength from confidence
        base_strength = signal.confidence
        
        # Apply timeframe weight
        timeframe_strength = base_strength * timeframe_weight
        
        # Apply strategy weight
        strategy_weight = self.strategy_weights.get(signal.strategy_id, 1.0)
        
        # Apply mode-specific adjustments
        mode_multipliers = {
            StrategyMode.DETERMINISTIC: 1.0,
            StrategyMode.ML_LIGHT: 1.1,
            StrategyMode.LLM_REASONING: 1.3
        }
        mode_multiplier = mode_multipliers.get(signal.strategy_mode, 1.0)
        
        return timeframe_strength * strategy_weight * mode_multiplier
    
    def _handle_conflict(self, signal: Signal, conflict_result: Dict[str, Any]) -> Dict[str, Any]:
        """Handle signal conflicts based on resolution strategy"""
        resolution_strategy = SIGNAL_CONFLICT_RESOLUTION
        timeframe_resolution = TIMEFRAME_CONFLICT_RESOLUTION
        
        # Check if timeframe-based resolution is enabled
        if timeframe_resolution in ["higher_timeframe_wins", "timeframe_weighted"]:
            return self._handle_timeframe_conflict(signal, conflict_result, timeframe_resolution)
        
        # Standard conflict resolution
        if resolution_strategy == ConflictResolutionStrategy.STRONGEST_WINS:
            if conflict_result["new_strength"] > conflict_result["existing_strength"]:
                # Cancel existing signals and execute new one
                self._cancel_opposing_signals(signal.symbol)
                return {"status": "executed", "reason": "Stronger signal won conflict"}
            else:
                return {"status": "rejected", "reason": "Weaker signal lost conflict"}
        
        elif resolution_strategy == ConflictResolutionStrategy.FIRST_COME_FIRST_SERVED:
            return {"status": "rejected", "reason": "Signal conflict - FCFS policy"}
        
        elif resolution_strategy == ConflictResolutionStrategy.MANUAL_REVIEW:
            return {"status": "pending_review", "reason": "Signal conflict requires manual review"}
        
        elif resolution_strategy == ConflictResolutionStrategy.WEIGHTED_AVERAGE:
            return self._handle_weighted_average_conflict(signal, conflict_result)
        
        else:
            return {"status": "rejected", "reason": "Signal conflict - no resolution strategy"}
    
    def _handle_weighted_average_conflict(self, signal: Signal, conflict_result: Dict[str, Any]) -> Dict[str, Any]:
        """Handle conflict using weighted average approach"""
        # Calculate weighted average of all signals
        all_signals = conflict_result["opposing_signals"] + [signal]
        total_weight = 0
        weighted_action = 0  # -1 for sell, 0 for hold, 1 for buy
        
        for s in all_signals:
            weight = self._calculate_signal_strength(s)
            action_value = {"sell": -1, "hold": 0, "buy": 1}.get(s.action, 0)
            weighted_action += action_value * weight
            total_weight += weight
        
        if total_weight > 0:
            final_action_value = weighted_action / total_weight
            
            # Determine final action
            if final_action_value > 0.3:
                final_action = "buy"
            elif final_action_value < -0.3:
                final_action = "sell"
            else:
                final_action = "hold"
            
            # Update signal with aggregated action
            signal.action = final_action
            return {"status": "executed", "reason": "Weighted average conflict resolution"}
        
        return {"status": "rejected", "reason": "Insufficient signal strength for weighted average"}
    
    def _handle_timeframe_conflict(self, signal: Signal, conflict_result: Dict[str, Any], resolution_strategy: str) -> Dict[str, Any]:
        """Handle conflicts using timeframe-based resolution strategies"""
        
        if resolution_strategy == "higher_timeframe_wins":
            # Higher timeframe signals win over lower timeframe signals
            new_timeframe_value = self._get_timeframe_numeric_value(signal.timeframe)
            existing_timeframe_value = max(
                self._get_timeframe_numeric_value(s.timeframe) 
                for s in conflict_result["opposing_signals"]
            )
            
            if new_timeframe_value > existing_timeframe_value:
                # Higher timeframe signal wins
                self._cancel_opposing_signals(signal.symbol)
                return {
                    "status": "executed", 
                    "reason": f"Higher timeframe signal won conflict ({signal.timeframe.value} vs {existing_timeframe_value})"
                }
            else:
                return {
                    "status": "rejected", 
                    "reason": f"Lower timeframe signal lost conflict ({signal.timeframe.value} vs {existing_timeframe_value})"
                }
        
        elif resolution_strategy == "timeframe_weighted":
            # Use weighted average considering timeframe strength
            if conflict_result["new_timeframe_strength"] > conflict_result["existing_timeframe_strength"]:
                # Higher timeframe-weighted signal wins
                self._cancel_opposing_signals(signal.symbol)
                return {
                    "status": "executed", 
                    "reason": "Higher timeframe-weighted signal won conflict"
                }
            else:
                return {
                    "status": "rejected", 
                    "reason": "Lower timeframe-weighted signal lost conflict"
                }
        
        else:
            return {"status": "rejected", "reason": f"Unknown timeframe resolution strategy: {resolution_strategy}"}
    
    def _get_timeframe_numeric_value(self, timeframe: TimeFrame) -> int:
        """Convert timeframe to numeric value for comparison"""
        timeframe_values = {
            TimeFrame.TICK: 1,
            TimeFrame.MINUTE_1: 2,
            TimeFrame.MINUTE_3: 3,
            TimeFrame.MINUTE_5: 4,
            TimeFrame.MINUTE_15: 5,
            TimeFrame.MINUTE_30: 6,
            TimeFrame.HOUR_1: 7,
            TimeFrame.HOUR_2: 8,
            TimeFrame.HOUR_4: 9,
            TimeFrame.HOUR_6: 10,
            TimeFrame.HOUR_8: 11,
            TimeFrame.HOUR_12: 12,
            TimeFrame.DAY_1: 13,
            TimeFrame.DAY_3: 14,
            TimeFrame.WEEK_1: 15,
            TimeFrame.MONTH_1: 16
        }
        return timeframe_values.get(timeframe, 1)
    
    def _store_signal(self, signal: Signal) -> None:
        """Store signal in active signals and history"""
        signal_key = f"{signal.strategy_id}_{signal.symbol}_{signal.timestamp.isoformat()}"
        self.active_signals[signal_key] = signal
        self.signal_history.append(signal)
        
        # Clean up old signals
        self._cleanup_old_signals()
    
    def _cleanup_old_signals(self) -> None:
        """Remove old signals from active signals"""
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        expired_keys = [
            key for key, signal in self.active_signals.items()
            if signal.timestamp < cutoff_time
        ]
        
        for key in expired_keys:
            del self.active_signals[key]
    
    def _cancel_opposing_signals(self, symbol: str) -> None:
        """Cancel opposing signals for a symbol"""
        keys_to_remove = [
            key for key, signal in self.active_signals.items()
            if signal.symbol == symbol
        ]
        
        for key in keys_to_remove:
            del self.active_signals[key]
    
    def set_strategy_weight(self, strategy_id: str, weight: float) -> None:
        """Set weight for a strategy"""
        self.strategy_weights[strategy_id] = weight
    
    def get_signal_summary(self) -> Dict[str, Any]:
        """Get summary of active signals"""
        mode_counts = defaultdict(int)
        for signal in self.active_signals.values():
            mode_counts[signal.strategy_mode.value] += 1
        
        return {
            "active_signals_count": len(self.active_signals),
            "total_signals_processed": len(self.signal_history),
            "daily_pnl": self.daily_pnl,
            "max_daily_loss": self.max_daily_loss,
            "strategy_weights": self.strategy_weights,
            "mode_distribution": dict(mode_counts)
        }


class DeterministicProcessor:
    """Deterministic rule-based signal processor"""
    
    async def process(self, signal: Signal, active_signals: Dict[str, Signal]) -> Dict[str, Any]:
        """Process signal using deterministic rules"""
        # Basic confidence threshold
        if signal.confidence < 0.6:
            return {"status": "rejected", "reason": "Confidence below threshold"}
        
        # Check for conflicting signals
        conflicting_signals = [
            s for s in active_signals.values()
            if s.symbol == signal.symbol and s.action != signal.action
        ]
        
        if conflicting_signals:
            # Use simple rule: higher confidence wins
            max_conflicting_confidence = max(s.confidence for s in conflicting_signals)
            if signal.confidence <= max_conflicting_confidence:
                return {"status": "rejected", "reason": "Lower confidence than conflicting signal"}
        
        # Calculate order parameters
        order_params = self._calculate_order_parameters(signal)
        
        return {
            "status": "executed",
            "signal": signal,
            "order_params": order_params,
            "confidence": signal.confidence,
            "reason": "Deterministic rules satisfied"
        }
    
    def _calculate_order_parameters(self, signal: Signal) -> Dict[str, Any]:
        """Calculate order parameters using deterministic rules"""
        params = {
            "symbol": signal.symbol,
            "side": signal.action,
            "type": signal.order_type.value,
            "time_in_force": signal.time_in_force.value,
        }
        
        # Position sizing based on confidence
        if signal.position_size_pct:
            # Scale position size by confidence
            scaled_size = signal.position_size_pct * signal.confidence
            params["position_size_pct"] = scaled_size
        
        # Risk management
        if signal.stop_loss_pct:
            params["stop_loss_pct"] = signal.stop_loss_pct
        
        if signal.take_profit_pct:
            params["take_profit_pct"] = signal.take_profit_pct
        
        return params


class MLProcessor:
    """Light ML model signal processor"""
    
    def __init__(self):
        self.model_loaded = False
        self.last_model_update = 0
    
    async def process(self, signal: Signal, active_signals: Dict[str, Signal]) -> Dict[str, Any]:
        """Process signal using ML models"""
        # Load model if needed
        await self._ensure_model_loaded()
        
        if not self.model_loaded:
            return {"status": "rejected", "reason": "ML model not available"}
        
        # Extract features
        features = self._extract_features(signal, active_signals)
        
        # Make prediction
        prediction = await self._make_prediction(features)
        
        if prediction["confidence"] < 0.5:
            return {"status": "rejected", "reason": "ML model confidence too low"}
        
        # Calculate order parameters
        order_params = self._calculate_order_parameters(signal, prediction)
        
        return {
            "status": "executed",
            "signal": signal,
            "order_params": order_params,
            "confidence": prediction["confidence"],
            "reason": "ML model approved signal",
            "ml_prediction": prediction
        }
    
    async def _ensure_model_loaded(self) -> None:
        """Ensure ML model is loaded"""
        # Placeholder for ML model loading
        self.model_loaded = True
    
    def _extract_features(self, signal: Signal, active_signals: Dict[str, Signal]) -> Dict[str, Any]:
        """Extract features for ML model"""
        features = {
            "confidence": signal.confidence,
            "strength": signal.strength.value,
            "action": signal.action,
            "order_type": signal.order_type.value,
            "current_price": signal.current_price,
            "target_price": signal.target_price or 0,
            "stop_loss_pct": signal.stop_loss_pct or 0,
            "take_profit_pct": signal.take_profit_pct or 0,
            "position_size_pct": signal.position_size_pct or 0,
        }
        
        # Add market context features
        if signal.indicators:
            features.update(signal.indicators)
        
        # Add conflicting signals count
        conflicting_count = len([
            s for s in active_signals.values()
            if s.symbol == signal.symbol and s.action != signal.action
        ])
        features["conflicting_signals"] = conflicting_count
        
        return features
    
    async def _make_prediction(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """Make prediction using ML model"""
        # Placeholder for ML prediction
        # In real implementation, this would call the actual ML model
        confidence = features.get("confidence", 0.5)
        
        return {
            "action": "buy" if confidence > 0.6 else "hold",
            "confidence": confidence,
            "features_used": list(features.keys())
        }
    
    def _calculate_order_parameters(self, signal: Signal, prediction: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate order parameters with ML insights"""
        params = {
            "symbol": signal.symbol,
            "side": signal.action,
            "type": signal.order_type.value,
            "time_in_force": signal.time_in_force.value,
        }
        
        # Adjust position size based on ML confidence
        if signal.position_size_pct:
            ml_confidence = prediction.get("confidence", signal.confidence)
            adjusted_size = signal.position_size_pct * ml_confidence
            params["position_size_pct"] = adjusted_size
        
        return params


class LLMProcessor:
    """LLM reasoning signal processor"""
    
    def __init__(self):
        self.llm_available = False
    
    async def process(self, signal: Signal, active_signals: Dict[str, Signal]) -> Dict[str, Any]:
        """Process signal using LLM reasoning"""
        # Check LLM availability
        if not self.llm_available:
            return {"status": "rejected", "reason": "LLM not available"}
        
        # Prepare context for LLM
        context = self._prepare_llm_context(signal, active_signals)
        
        # Get LLM reasoning
        reasoning = await self._get_llm_reasoning(context)
        
        if not reasoning["approved"]:
            return {
                "status": "rejected", 
                "reason": reasoning.get("reason", "LLM rejected signal"),
                "llm_reasoning": reasoning
            }
        
        # Calculate order parameters
        order_params = self._calculate_order_parameters(signal, reasoning)
        
        return {
            "status": "executed",
            "signal": signal,
            "order_params": order_params,
            "confidence": reasoning.get("confidence", signal.confidence),
            "reason": "LLM reasoning approved signal",
            "llm_reasoning": reasoning
        }
    
    def _prepare_llm_context(self, signal: Signal, active_signals: Dict[str, Signal]) -> Dict[str, Any]:
        """Prepare context for LLM reasoning"""
        context = {
            "signal": {
                "strategy_id": signal.strategy_id,
                "symbol": signal.symbol,
                "action": signal.action,
                "confidence": signal.confidence,
                "strength": signal.strength.value,
                "current_price": signal.current_price,
                "target_price": signal.target_price,
                "rationale": signal.rationale,
                "indicators": signal.indicators or {},
                "meta": signal.meta
            },
            "market_context": {
                "active_signals_count": len(active_signals),
                "conflicting_signals": [
                    {
                        "strategy_id": s.strategy_id,
                        "action": s.action,
                        "confidence": s.confidence
                    }
                    for s in active_signals.values()
                    if s.symbol == signal.symbol and s.action != signal.action
                ]
            }
        }
        
        return context
    
    async def _get_llm_reasoning(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Get LLM reasoning for signal"""
        # Placeholder for LLM reasoning
        # In real implementation, this would call the actual LLM API
        
        # Simulate LLM reasoning
        confidence = context["signal"]["confidence"]
        approved = confidence > 0.7
        
        return {
            "approved": approved,
            "confidence": confidence,
            "reasoning": f"Signal confidence is {confidence:.2f}, which is {'sufficient' if approved else 'insufficient'} for execution",
            "alternatives_considered": [
                "Hold position",
                "Reduce position size",
                "Wait for better entry"
            ],
            "risk_assessment": "Medium risk due to market volatility"
        }
    
    def _calculate_order_parameters(self, signal: Signal, reasoning: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate order parameters with LLM insights"""
        params = {
            "symbol": signal.symbol,
            "side": signal.action,
            "type": signal.order_type.value,
            "time_in_force": signal.time_in_force.value,
        }
        
        # LLM might suggest position size adjustments
        llm_confidence = reasoning.get("confidence", signal.confidence)
        if signal.position_size_pct:
            # LLM might be more conservative
            adjusted_size = signal.position_size_pct * min(llm_confidence, 0.8)
            params["position_size_pct"] = adjusted_size
        
        return params


# Global signal aggregator instance
signal_aggregator = SignalAggregator() 