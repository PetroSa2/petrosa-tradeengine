"""
Pydantic models for performance metrics responses.

These models provide structured, agent-friendly responses for querying
performance metrics, success rates, and resource usage across the system.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LatencyMetrics(BaseModel):
    """Latency metrics with percentiles"""

    p50: float = Field(..., description="50th percentile latency in milliseconds")
    p95: float = Field(..., description="95th percentile latency in milliseconds")
    p99: float = Field(..., description="99th percentile latency in milliseconds")
    unit: str = Field(default="ms", description="Unit of measurement")


class ThroughputMetrics(BaseModel):
    """Throughput metrics"""

    requests_per_second: float = Field(..., description="Average requests per second")
    total_requests: int = Field(..., description="Total requests in time window")
    unit: str = Field(default="rps", description="Unit of measurement")


class ErrorMetrics(BaseModel):
    """Error rate metrics with breakdown"""

    error_rate: float = Field(..., description="Error rate as decimal (0.002 = 0.2%)")
    total_errors: int = Field(..., description="Total errors in time window")
    error_types: Dict[str, int] = Field(
        default_factory=dict, description="Breakdown by error type"
    )


class ResourceUsageMetrics(BaseModel):
    """Resource utilization metrics"""

    cpu_percent: float = Field(..., description="Average CPU usage percentage")
    memory_mb: float = Field(..., description="Average memory usage in MB")
    pod_count: int = Field(default=1, description="Number of pods running")


class PerformanceMetricsData(BaseModel):
    """Performance metrics data"""

    latency: Optional[LatencyMetrics] = None
    throughput: Optional[ThroughputMetrics] = None
    errors: Optional[ErrorMetrics] = None
    resource_usage: Optional[ResourceUsageMetrics] = None


class PerformanceMetricsResponse(BaseModel):
    """Complete performance metrics response"""

    success: bool = True
    timeframe: str = Field(..., description="Time window queried (e.g., '1h', '24h')")
    metrics: PerformanceMetricsData
    sample_count: int = Field(..., description="Number of samples in aggregation")
    collection_timestamp: datetime = Field(
        ..., description="Timestamp when metrics were collected"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class SuccessMetrics(BaseModel):
    """Success rate metrics for strategies"""

    signals_generated: int = Field(..., description="Total signals generated")
    signals_executed: int = Field(..., description="Signals successfully executed")
    execution_rate: float = Field(
        ..., description="Execution rate as decimal (0.784 = 78.4%)"
    )
    winning_trades: int = Field(default=0, description="Number of winning trades")
    losing_trades: int = Field(default=0, description="Number of losing trades")
    win_rate: float = Field(default=0.0, description="Win rate as decimal")
    avg_profit_per_trade: float = Field(
        default=0.0, description="Average profit per trade"
    )
    total_pnl: float = Field(default=0.0, description="Total profit and loss")


class SymbolBreakdown(BaseModel):
    """Per-symbol performance breakdown"""

    win_rate: float
    trades: int
    pnl: float = 0.0


class SuccessRateResponse(BaseModel):
    """Success rate response"""

    success: bool = True
    strategy_id: Optional[str] = None
    window: str = Field(..., description="Time window")
    success_metrics: SuccessMetrics
    symbol_breakdown: Dict[str, SymbolBreakdown] = Field(default_factory=dict)


class ResourceMetrics(BaseModel):
    """Detailed resource metrics"""

    current_percent: float = Field(..., description="Current utilization percentage")
    avg_percent: float = Field(..., description="Average utilization percentage")
    max_percent: float = Field(..., description="Maximum utilization percentage")
    limit: Optional[str] = Field(None, description="Resource limit (if applicable)")


class MemoryMetrics(BaseModel):
    """Memory-specific metrics"""

    current_mb: float = Field(..., description="Current memory usage in MB")
    avg_mb: float = Field(..., description="Average memory usage in MB")
    max_mb: float = Field(..., description="Maximum memory usage in MB")
    limit_mb: Optional[int] = Field(None, description="Memory limit in MB")


class ResourceUsageResponse(BaseModel):
    """Resource usage response"""

    success: bool = True
    pod_id: Optional[str] = None
    timeframe: str
    resources: Dict[str, Any] = Field(default_factory=dict)
    pod_count: int = Field(default=1)
    pod_restarts: int = Field(default=0)


class DataPoint(BaseModel):
    """Single data point for trend analysis"""

    timestamp: datetime = Field(..., description="Timestamp of the data point")
    value: float = Field(..., description="Metric value at this timestamp")


class Anomaly(BaseModel):
    """Detected anomaly in metric trend"""

    timestamp: datetime
    value: float
    deviation: float = Field(..., description="Standard deviations from mean")


class TrendAnalysis(BaseModel):
    """Trend analysis statistics"""

    direction: str = Field(
        ..., description="Trend direction: 'increasing', 'decreasing', or 'stable'"
    )
    slope: float = Field(..., description="Rate of change per time unit")
    correlation: float = Field(..., description="Trend strength (0-1)")
    anomalies: List[Anomaly] = Field(default_factory=list)


class TrendResponse(BaseModel):
    """Metric trend response"""

    success: bool = True
    metric: str = Field(..., description="Metric name")
    period: str = Field(..., description="Time period covered")
    interval: str = Field(..., description="Data point interval")
    data_points: List[DataPoint]
    trend_analysis: TrendAnalysis


class MetricChange(BaseModel):
    """Change in metric before vs after"""

    before: float
    after: float
    change_percent: float = Field(..., description="Percentage change")
    improvement: bool = Field(
        ..., description="Whether change is improvement (depends on metric)"
    )


class ComparisonResponse(BaseModel):
    """Metrics comparison response"""

    success: bool = True
    before_timestamp: datetime
    after_timestamp: datetime
    window_seconds: int
    comparison: Dict[str, MetricChange] = Field(default_factory=dict)
    overall_assessment: str = Field(
        ..., description="Overall assessment: 'improved', 'degraded', or 'mixed'"
    )
    recommendation: str = Field(..., description="Human-readable recommendation")
