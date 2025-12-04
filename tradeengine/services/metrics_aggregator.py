"""
Metrics aggregation service for querying performance metrics.

This service provides a unified interface to query performance metrics from
Prometheus (real-time) and MongoDB (historical), with intelligent caching
to prevent query overload.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from tradeengine.models.metrics import (
    ErrorMetrics,
    LatencyMetrics,
    ResourceUsageMetrics,
    ThroughputMetrics,
)

logger = logging.getLogger(__name__)


class MetricsAggregator:
    """
    Aggregates performance metrics from various sources.

    Uses hybrid approach:
    - Recent data (< 24h): Query Prometheus
    - Historical data (> 24h): Query MongoDB (if available)
    """

    def __init__(self):
        """Initialize metrics aggregator"""
        self.cache: Dict[str, Any] = {}
        self.cache_ttl_seconds = 60  # Cache metrics for 1 minute

    def parse_timeframe(self, timeframe: str) -> int:
        """
        Parse timeframe string to seconds.

        Args:
            timeframe: Time window like '1h', '24h', '7d', '30d'

        Returns:
            Number of seconds in the timeframe

        Raises:
            ValueError: If timeframe format is invalid
        """
        timeframe = timeframe.lower().strip()

        # Parse number and unit
        if timeframe.endswith("m"):
            multiplier = 60
            value = timeframe[:-1]
        elif timeframe.endswith("h"):
            multiplier = 3600
            value = timeframe[:-1]
        elif timeframe.endswith("d"):
            multiplier = 86400
            value = timeframe[:-1]
        else:
            raise ValueError(
                f"Invalid timeframe format: {timeframe}. Use format like '1h', '24h', '7d'"
            )

        try:
            return int(value) * multiplier
        except ValueError as e:
            raise ValueError(
                f"Invalid timeframe value: {value}. Must be a number."
            ) from e

    async def get_metrics(
        self,
        start_time: datetime,
        metric_filter: Optional[str] = None,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregated metrics for the specified time window.

        Args:
            start_time: Start of time window
            metric_filter: Specific metric to return (None = all)
            strategy_id: Filter by strategy ID
            symbol: Filter by symbol

        Returns:
            Dictionary with metric data
        """
        try:
            # Check cache first
            cache_key = self._get_cache_key(
                start_time, metric_filter, strategy_id, symbol
            )
            if cache_key in self.cache:
                cached_data, cached_time = self.cache[cache_key]
                if (
                    datetime.utcnow() - cached_time
                ).total_seconds() < self.cache_ttl_seconds:
                    logger.debug(f"Returning cached metrics for key: {cache_key}")
                    return cached_data

            # Calculate time window
            end_time = datetime.utcnow()
            window_seconds = (end_time - start_time).total_seconds()

            # Aggregate metrics
            metrics_data = {
                "latency": await self._get_latency_metrics(
                    start_time, end_time, strategy_id, symbol
                ),
                "throughput": await self._get_throughput_metrics(
                    start_time, end_time, strategy_id, symbol
                ),
                "errors": await self._get_error_metrics(
                    start_time, end_time, strategy_id, symbol
                ),
                "resource_usage": await self._get_resource_metrics(
                    start_time, end_time
                ),
            }

            # Filter specific metric if requested
            if metric_filter:
                if metric_filter in metrics_data:
                    metrics_data = {metric_filter: metrics_data[metric_filter]}
                else:
                    logger.warning(f"Requested metric not found: {metric_filter}")

            # Cache result
            self.cache[cache_key] = (metrics_data, datetime.utcnow())

            return metrics_data

        except Exception as e:
            logger.error(f"Error aggregating metrics: {e}")
            # Return empty metrics structure on error
            return {
                "latency": None,
                "throughput": None,
                "errors": None,
                "resource_usage": None,
            }

    async def _get_latency_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        strategy_id: Optional[str],
        symbol: Optional[str],
    ) -> Optional[LatencyMetrics]:
        """
        Get latency metrics from monitoring system.

        In a real implementation, this would query Prometheus or similar.
        For now, returns mock data.
        """
        try:
            # TODO: Implement actual Prometheus query
            # Example PromQL query:
            # histogram_quantile(0.95,
            #   rate(signal_processing_duration_seconds_bucket[5m])
            # )

            # Mock data for now
            return LatencyMetrics(p50=45.2, p95=120.5, p99=280.3, unit="ms")
        except Exception as e:
            logger.error(f"Error fetching latency metrics: {e}")
            return None

    async def _get_throughput_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        strategy_id: Optional[str],
        symbol: Optional[str],
    ) -> Optional[ThroughputMetrics]:
        """Get throughput metrics"""
        try:
            # TODO: Implement actual Prometheus query
            # Example PromQL query:
            # rate(signals_generated_total[5m])

            # Mock data for now
            window_seconds = (end_time - start_time).total_seconds()
            estimated_requests = int(window_seconds * 125.3)  # Mock RPS

            return ThroughputMetrics(
                requests_per_second=125.3, total_requests=estimated_requests, unit="rps"
            )
        except Exception as e:
            logger.error(f"Error fetching throughput metrics: {e}")
            return None

    async def _get_error_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        strategy_id: Optional[str],
        symbol: Optional[str],
    ) -> Optional[ErrorMetrics]:
        """Get error rate metrics"""
        try:
            # TODO: Implement actual Prometheus query
            # Example PromQL query:
            # rate(errors_total[5m]) / rate(requests_total[5m])

            # Mock data for now
            return ErrorMetrics(
                error_rate=0.002,  # 0.2%
                total_errors=900,
                error_types={"validation": 450, "timeout": 300, "internal": 150},
            )
        except Exception as e:
            logger.error(f"Error fetching error metrics: {e}")
            return None

    async def _get_resource_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[ResourceUsageMetrics]:
        """Get resource usage metrics"""
        try:
            # TODO: Implement actual Kubernetes/Prometheus query
            # Example PromQL queries:
            # - container_cpu_usage_seconds_total
            # - container_memory_usage_bytes

            # Mock data for now
            return ResourceUsageMetrics(cpu_percent=45.2, memory_mb=512.5, pod_count=3)
        except Exception as e:
            logger.error(f"Error fetching resource metrics: {e}")
            return None

    async def get_success_rates(
        self,
        strategy_id: Optional[str],
        window: str,
        symbol: Optional[str],
    ) -> Dict[str, Any]:
        """
        Get success rate metrics for strategies.

        Args:
            strategy_id: Filter by strategy ID (None = all strategies)
            window: Time window ('1h', '24h', '7d')
            symbol: Filter by symbol

        Returns:
            Success rate metrics
        """
        try:
            # TODO: Implement actual MongoDB query of trade logs
            # Query the signals collection and trades collection to calculate:
            # - signals_generated
            # - signals_executed
            # - winning_trades
            # - losing_trades
            # - PnL

            # Mock data for now
            return {
                "signals_generated": 125,
                "signals_executed": 98,
                "execution_rate": 0.784,
                "winning_trades": 62,
                "losing_trades": 36,
                "win_rate": 0.633,
                "avg_profit_per_trade": 125.50,
                "total_pnl": 4500.25,
                "symbol_breakdown": {
                    "BTCUSDT": {"win_rate": 0.65, "trades": 60, "pnl": 2800.0},
                    "ETHUSDT": {"win_rate": 0.61, "trades": 38, "pnl": 1700.25},
                },
            }
        except Exception as e:
            logger.error(f"Error fetching success rates: {e}")
            return {}

    async def get_resource_usage(
        self,
        pod_id: Optional[str],
        timeframe: str,
    ) -> Dict[str, Any]:
        """
        Get resource utilization for pods.

        Args:
            pod_id: Specific pod ID (None = all pods)
            timeframe: Time window

        Returns:
            Resource usage data
        """
        try:
            # TODO: Implement actual Kubernetes API query
            # Use Kubernetes metrics API to get:
            # - CPU usage
            # - Memory usage
            # - Pod restarts

            # Mock data for now
            return {
                "cpu": {
                    "current_percent": 45.2,
                    "avg_percent": 42.1,
                    "max_percent": 78.5,
                    "limit": "1000m",
                },
                "memory": {
                    "current_mb": 512.5,
                    "avg_mb": 485.2,
                    "max_mb": 650.3,
                    "limit_mb": 1024,
                },
                "pod_count": 3,
                "pod_restarts": 0,
            }
        except Exception as e:
            logger.error(f"Error fetching resource usage: {e}")
            return {}

    async def get_metric_trends(
        self,
        metric: str,
        period: str,
        interval: str,
        strategy_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get metric evolution over time.

        Args:
            metric: Metric name
            period: Total time period
            interval: Data point interval
            strategy_id: Filter by strategy

        Returns:
            Trend data with analysis
        """
        try:
            # TODO: Implement actual time-series query
            # Query Prometheus or MongoDB for historical data points

            # Mock data for now
            period_seconds = self.parse_timeframe(period)
            interval_seconds = self.parse_timeframe(interval)
            num_points = min(
                period_seconds // interval_seconds, 100
            )  # Limit to 100 points

            # Generate mock data points
            base_time = datetime.utcnow() - timedelta(seconds=period_seconds)
            data_points = []
            base_value = 120.5
            for i in range(int(num_points)):
                timestamp = base_time + timedelta(seconds=i * interval_seconds)
                # Add slight upward trend with noise
                value = base_value + (i * 0.5) + ((i % 10) * 2)
                data_points.append({"timestamp": timestamp, "value": value})

            return {
                "data_points": data_points,
                "trend_analysis": {
                    "direction": "increasing",
                    "slope": 1.2,
                    "correlation": 0.85,
                    "anomalies": [],
                },
            }
        except Exception as e:
            logger.error(f"Error fetching metric trends: {e}")
            return {"data_points": [], "trend_analysis": {}}

    async def compare_metrics(
        self,
        before: datetime,
        after: datetime,
        metric: Optional[str],
        window: int,
    ) -> Dict[str, Any]:
        """
        Compare metrics before and after a timestamp.

        Args:
            before: Timestamp to compare before
            after: Timestamp to compare after
            metric: Specific metric (None = all)
            window: Time window around timestamps in seconds

        Returns:
            Comparison data
        """
        try:
            # TODO: Implement actual before/after comparison query
            # Query metrics for [before - window, before] and [after, after + window]

            # Mock data for now
            comparison = {
                "latency_p95": {
                    "before": 120.5,
                    "after": 95.2,
                    "change_percent": -21.0,
                    "improvement": True,
                },
                "throughput": {
                    "before": 125.3,
                    "after": 145.8,
                    "change_percent": 16.4,
                    "improvement": True,
                },
                "error_rate": {
                    "before": 0.002,
                    "after": 0.003,
                    "change_percent": 50.0,
                    "improvement": False,
                },
            }

            # Calculate overall assessment
            improvements = sum(
                1 for m in comparison.values() if m.get("improvement", False)
            )
            degradations = sum(
                1 for m in comparison.values() if not m.get("improvement", True)
            )

            if improvements > degradations:
                overall = "improved"
                recommendation = "Configuration change had positive impact"
            elif degradations > improvements:
                overall = "degraded"
                recommendation = (
                    "Configuration change had negative impact - consider reverting"
                )
            else:
                overall = "mixed"
                recommendation = (
                    "Configuration change had mixed results - further analysis needed"
                )

            return {
                "comparison": comparison,
                "overall_assessment": overall,
                "recommendation": recommendation,
            }
        except Exception as e:
            logger.error(f"Error comparing metrics: {e}")
            return {
                "comparison": {},
                "overall_assessment": "unknown",
                "recommendation": "",
            }

    def _get_cache_key(
        self,
        start_time: datetime,
        metric_filter: Optional[str],
        strategy_id: Optional[str],
        symbol: Optional[str],
    ) -> str:
        """Generate cache key for metrics query"""
        return f"{start_time.isoformat()}:{metric_filter}:{strategy_id}:{symbol}"
