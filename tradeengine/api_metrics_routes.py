"""
Performance Metrics API for TA Bot.

Provides agent-friendly REST endpoints to query performance metrics,
success rates, latency trends, and resource utilization.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from tradeengine.models.metrics import (
    ComparisonResponse,
    DataPoint,
    MetricChange,
    PerformanceMetricsData,
    PerformanceMetricsResponse,
    ResourceUsageResponse,
    SuccessMetrics,
    SuccessRateResponse,
    SymbolBreakdown,
    TrendAnalysis,
    TrendResponse,
)
from tradeengine.services.metrics_aggregator import MetricsAggregator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

# Global metrics aggregator instance
_metrics_aggregator: Optional[MetricsAggregator] = None


def get_metrics_aggregator() -> MetricsAggregator:
    """Get or create metrics aggregator instance"""
    global _metrics_aggregator
    if _metrics_aggregator is None:
        _metrics_aggregator = MetricsAggregator()
    return _metrics_aggregator


@router.get(
    "/performance",
    response_model=PerformanceMetricsResponse,
    summary="Get performance metrics",
    description="""
    **For LLM Agents**: Query performance metrics to evaluate configuration changes.

    Returns latency, throughput, error rates with time windows.
    Use this after configuration changes to measure impact.

    **Example**: `GET /api/v1/metrics/performance?timeframe=1h&metric=latency`

    **Time Windows**:
    - `1m` - 1 minute
    - `5m` - 5 minutes
    - `1h` - 1 hour
    - `24h` - 24 hours
    - `7d` - 7 days
    - `30d` - 30 days

    **Metrics**:
    - `latency` - Response time percentiles (p50, p95, p99)
    - `throughput` - Requests per second
    - `errors` - Error rates and types
    - `cpu` - CPU usage
    - `memory` - Memory usage
    """,
)
async def get_performance_metrics(
    timeframe: str = Query(
        "1h",
        description="Time window: 1m, 5m, 1h, 24h, 7d, 30d",
        regex="^[0-9]+(m|h|d)$",
    ),
    metric: Optional[str] = Query(
        None,
        description="Specific metric: latency, throughput, errors, cpu, memory",
    ),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    symbol: Optional[str] = Query(None, description="Filter by trading symbol"),
) -> PerformanceMetricsResponse:
    """
    Get performance metrics aggregated over time window.

    Returns comprehensive performance data including latency percentiles,
    throughput, error rates, and resource usage.
    """
    try:
        aggregator = get_metrics_aggregator()

        # Parse timeframe and calculate start time
        window_seconds = aggregator.parse_timeframe(timeframe)
        from datetime import timedelta

        start_time = datetime.utcnow() - timedelta(seconds=window_seconds)

        # Get metrics
        metrics_dict = await aggregator.get_metrics(
            start_time=start_time,
            metric_filter=metric,
            strategy_id=strategy_id,
            symbol=symbol,
        )

        # Build response
        metrics_data = PerformanceMetricsData(
            latency=metrics_dict.get("latency"),
            throughput=metrics_dict.get("throughput"),
            errors=metrics_dict.get("errors"),
            resource_usage=metrics_dict.get("resource_usage"),
        )

        # Calculate sample count (mock for now)
        sample_count = int(window_seconds / 10)  # Assuming 10-second samples

        return PerformanceMetricsResponse(
            success=True,
            timeframe=timeframe,
            metrics=metrics_data,
            sample_count=sample_count,
            collection_timestamp=datetime.utcnow(),
            metadata={
                "window_seconds": window_seconds,
                "strategy_id": strategy_id,
                "symbol": symbol,
            },
        )

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching performance metrics: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to fetch performance metrics"
        )


@router.get(
    "/success-rates",
    response_model=SuccessRateResponse,
    summary="Get success rates by strategy",
    description="""
    **For LLM Agents**: Measure how often strategies succeed.

    Returns win rate, signal quality, execution success for strategies.
    Use this to evaluate strategy effectiveness and identify underperforming strategies.

    **Example**: `GET /api/v1/metrics/success-rates?strategy_id=rsi_extreme_reversal&window=24h`
    """,
)
async def get_success_rates(
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    window: str = Query(
        "24h",
        description="Time window: 1h, 24h, 7d",
        regex="^[0-9]+(m|h|d)$",
    ),
    symbol: Optional[str] = Query(None, description="Filter by trading symbol"),
) -> SuccessRateResponse:
    """
    Get success rate metrics for strategies.

    Returns execution rates, win rates, and profitability metrics.
    """
    try:
        aggregator = get_metrics_aggregator()

        # Get success rate data
        success_data = await aggregator.get_success_rates(
            strategy_id=strategy_id,
            window=window,
            symbol=symbol,
        )

        # Build response
        success_metrics = SuccessMetrics(
            signals_generated=success_data.get("signals_generated", 0),
            signals_executed=success_data.get("signals_executed", 0),
            execution_rate=success_data.get("execution_rate", 0.0),
            winning_trades=success_data.get("winning_trades", 0),
            losing_trades=success_data.get("losing_trades", 0),
            win_rate=success_data.get("win_rate", 0.0),
            avg_profit_per_trade=success_data.get("avg_profit_per_trade", 0.0),
            total_pnl=success_data.get("total_pnl", 0.0),
        )

        # Parse symbol breakdown
        symbol_breakdown = {}
        for sym, data in success_data.get("symbol_breakdown", {}).items():
            symbol_breakdown[sym] = SymbolBreakdown(
                win_rate=data.get("win_rate", 0.0),
                trades=data.get("trades", 0),
                pnl=data.get("pnl", 0.0),
            )

        return SuccessRateResponse(
            success=True,
            strategy_id=strategy_id,
            window=window,
            success_metrics=success_metrics,
            symbol_breakdown=symbol_breakdown,
        )

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching success rates: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch success rates")


@router.get(
    "/resource-usage",
    response_model=ResourceUsageResponse,
    summary="Get resource utilization metrics",
    description="""
    **For LLM Agents**: Monitor resource usage to detect configuration issues.

    High CPU/memory after config change indicates problem.
    Use this to identify resource-intensive configurations.

    **Example**: `GET /api/v1/metrics/resource-usage?timeframe=1h`
    """,
)
async def get_resource_usage(
    pod_id: Optional[str] = Query(None, description="Specific pod ID"),
    timeframe: str = Query(
        "1h", description="Time window: 1h, 24h, 7d", regex="^[0-9]+(m|h|d)$"
    ),
) -> ResourceUsageResponse:
    """
    Get resource utilization metrics.

    Returns CPU, memory usage, pod count, and restart information.
    """
    try:
        aggregator = get_metrics_aggregator()

        # Get resource usage data
        resource_data = await aggregator.get_resource_usage(
            pod_id=pod_id,
            timeframe=timeframe,
        )

        return ResourceUsageResponse(
            success=True,
            pod_id=pod_id,
            timeframe=timeframe,
            resources=resource_data,
            pod_count=resource_data.get("pod_count", 1),
            pod_restarts=resource_data.get("pod_restarts", 0),
        )

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching resource usage: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch resource usage")


@router.get(
    "/trends",
    response_model=TrendResponse,
    summary="Get metric trends over time",
    description="""
    **For LLM Agents**: Analyze how metrics change over time.

    Useful for identifying gradual performance degradation or improvement.
    Returns time-series data with trend analysis.

    **Example**: `GET /api/v1/metrics/trends?metric=latency_p95&period=7d&interval=1h`
    """,
)
async def get_metric_trends(
    metric: str = Query(
        ...,
        description="Metric name: latency_p95, throughput, error_rate, etc.",
    ),
    period: str = Query(
        "7d", description="Time period: 1d, 7d, 30d", regex="^[0-9]+(d)$"
    ),
    interval: str = Query(
        "1h",
        description="Data point interval: 5m, 1h, 1d",
        regex="^[0-9]+(m|h|d)$",
    ),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
) -> TrendResponse:
    """
    Get metric evolution over time with data points at intervals.

    Returns time-series data and trend analysis including direction,
    slope, correlation, and detected anomalies.
    """
    try:
        aggregator = get_metrics_aggregator()

        # Get trend data
        trend_data = await aggregator.get_metric_trends(
            metric=metric,
            period=period,
            interval=interval,
            strategy_id=strategy_id,
        )

        # Parse data points
        data_points = [
            DataPoint(timestamp=dp["timestamp"], value=dp["value"])
            for dp in trend_data.get("data_points", [])
        ]

        # Parse trend analysis
        analysis = trend_data.get("trend_analysis", {})
        trend_analysis = TrendAnalysis(
            direction=analysis.get("direction", "stable"),
            slope=analysis.get("slope", 0.0),
            correlation=analysis.get("correlation", 0.0),
            anomalies=analysis.get("anomalies", []),
        )

        return TrendResponse(
            success=True,
            metric=metric,
            period=period,
            interval=interval,
            data_points=data_points,
            trend_analysis=trend_analysis,
        )

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching metric trends: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metric trends")


@router.get(
    "/comparison",
    response_model=ComparisonResponse,
    summary="Compare metrics before and after timestamp",
    description="""
    **For LLM Agents**: Compare performance before/after configuration changes.

    Provide timestamps of config change, get before/after comparison.
    Use this to measure the impact of configuration changes.

    **Example**: `GET /api/v1/metrics/comparison?before=2024-10-24T10:00:00Z&after=2024-10-24T11:00:00Z&window=3600`
    """,
)
async def compare_metrics(
    before: datetime = Query(..., description="Timestamp to compare before"),
    after: datetime = Query(..., description="Timestamp to compare after"),
    metric: Optional[str] = Query(None, description="Specific metric to compare"),
    window: int = Query(
        3600, description="Time window around timestamps (seconds)", ge=60, le=86400
    ),
) -> ComparisonResponse:
    """
    Compare metrics before and after a specific timestamp.

    Returns detailed comparison showing changes, improvement status,
    and recommendations.
    """
    try:
        # Validate timestamps
        if after <= before:
            raise ValueError("'after' timestamp must be later than 'before'")

        aggregator = get_metrics_aggregator()

        # Get comparison data
        comparison_data = await aggregator.compare_metrics(
            before=before,
            after=after,
            metric=metric,
            window=window,
        )

        # Parse comparison results
        comparison = {}
        for metric_name, metric_data in comparison_data.get("comparison", {}).items():
            comparison[metric_name] = MetricChange(
                before=metric_data["before"],
                after=metric_data["after"],
                change_percent=metric_data["change_percent"],
                improvement=metric_data["improvement"],
            )

        return ComparisonResponse(
            success=True,
            before_timestamp=before,
            after_timestamp=after,
            window_seconds=window,
            comparison=comparison,
            overall_assessment=comparison_data.get("overall_assessment", "unknown"),
            recommendation=comparison_data.get("recommendation", ""),
        )

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error comparing metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to compare metrics")


@router.get(
    "/health",
    summary="Metrics API health check",
    description="Verify metrics API is operational",
)
async def metrics_health():
    """Health check for metrics API"""
    return {
        "status": "healthy",
        "service": "ta-bot-metrics-api",
        "timestamp": datetime.utcnow().isoformat(),
    }
