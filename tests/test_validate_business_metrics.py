"""
Tests for validate_business_metrics.py script

Tests the validation script to ensure it correctly validates business metrics.
"""

from unittest.mock import MagicMock, patch

import pytest

from scripts.validate_business_metrics import (
    EXPECTED_METRICS,
    fetch_metrics,
    parse_metrics,
    validate_metric_values,
    validate_metrics_present,
)


class TestValidateBusinessMetrics:
    """Test business metrics validation script."""

    def test_expected_metrics_count(self):
        """Test that we have exactly 11 expected metrics."""
        assert len(EXPECTED_METRICS) == 11

    def test_fetch_metrics_success(self):
        """Test fetching metrics from endpoint."""
        mock_response = MagicMock()
        mock_response.text = "# HELP test_metric Test metric\n# TYPE test_metric counter\ntest_metric 1.0\n"
        mock_response.raise_for_status = MagicMock()

        with patch(
            "scripts.validate_business_metrics.requests.get", return_value=mock_response
        ):
            result = fetch_metrics("http://localhost:9090/metrics")
            assert result == mock_response.text

    def test_fetch_metrics_failure(self):
        """Test fetch_metrics handles errors."""
        # Mock requests.get to raise RequestException (which fetch_metrics catches)
        from requests.exceptions import RequestException

        with patch(
            "scripts.validate_business_metrics.requests.get",
            side_effect=RequestException("Connection error"),
        ):
            # fetch_metrics calls sys.exit(1) on RequestException, which raises SystemExit
            with pytest.raises(SystemExit) as exc_info:
                fetch_metrics("http://localhost:9090/metrics")
            assert exc_info.value.code == 1

    def test_parse_metrics(self):
        """Test parsing Prometheus metrics text."""
        metrics_text = """# HELP tradeengine_orders_executed_by_type_total Total orders executed
# TYPE tradeengine_orders_executed_by_type_total counter
tradeengine_orders_executed_by_type_total{order_type="market",side="buy",symbol="BTCUSDT",exchange="binance"} 10.0
        """
        metrics = parse_metrics(metrics_text)
        # parse_metrics returns a dict with metric names as keys (without _total suffix), each containing a list
        assert "tradeengine_orders_executed_by_type" in metrics
        assert len(metrics["tradeengine_orders_executed_by_type"]) > 0
        assert (
            metrics["tradeengine_orders_executed_by_type"][0]["name"]
            == "tradeengine_orders_executed_by_type_total"
        )

    def test_validate_metrics_present_all_found(self):
        """Test validation when all metrics are present."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_order_execution_latency_seconds_bucket": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_order_failures_total": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_risk_rejections_total": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_risk_checks_total": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_current_position_size": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_total_position_value_usd": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_total_realized_pnl_usd": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_total_unrealized_pnl_usd": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_total_daily_pnl_usd": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
            "tradeengine_order_success_rate": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
        }
        all_present, missing = validate_metrics_present(metrics)
        assert all_present is True
        assert len(missing) == 0

    def test_validate_metrics_present_missing(self):
        """Test validation when metrics are missing."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": 1.0, "labels": {}}
            ],
        }
        all_present, missing = validate_metrics_present(metrics)
        assert all_present is False
        assert len(missing) > 0

    def test_validate_metric_values_nan(self):
        """Test validation detects NaN values."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": float("nan"), "labels": {}}
            ],
        }
        values_ok, issues = validate_metric_values(metrics)
        assert values_ok is False
        assert any("NaN" in issue for issue in issues)

    def test_validate_metric_values_inf(self):
        """Test validation detects Inf values."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": float("inf"), "labels": {}}
            ],
        }
        values_ok, issues = validate_metric_values(metrics)
        assert values_ok is False
        assert any("Inf" in issue for issue in issues)

    def test_validate_metric_values_negative_counter(self):
        """Test validation detects negative counter values."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": -1.0, "labels": {}}
            ],
        }
        values_ok, issues = validate_metric_values(metrics)
        assert values_ok is False
        assert any("negative" in issue.lower() for issue in issues)

    def test_validate_metric_values_valid(self):
        """Test validation passes for valid values."""
        metrics = {
            "tradeengine_orders_executed_by_type_total": [
                {"name": "test", "value": 10.0, "labels": {}}
            ],
            "tradeengine_current_position_size": [
                {"name": "test", "value": 0.5, "labels": {}}
            ],
        }
        values_ok, issues = validate_metric_values(metrics)
        assert values_ok is True
        assert len(issues) == 0

    @patch("scripts.validate_business_metrics.fetch_metrics")
    @patch("scripts.validate_business_metrics.parse_metrics")
    @patch("scripts.validate_business_metrics.validate_metrics_present")
    @patch("scripts.validate_business_metrics.validate_metric_values")
    @patch(
        "sys.argv",
        ["validate_business_metrics.py", "--endpoint", "http://localhost:9090/metrics"],
    )
    def test_main_success(
        self, mock_validate_values, mock_validate_present, mock_parse, mock_fetch
    ):
        """Test main() function with successful validation."""
        from scripts.validate_business_metrics import main

        # Mock to return metrics that include all expected business metrics
        mock_fetch.return_value = """# TYPE tradeengine_orders_executed_by_type_total counter
tradeengine_orders_executed_by_type_total{order_type="market"} 1.0
# TYPE tradeengine_order_execution_latency_seconds histogram
tradeengine_order_execution_latency_seconds_bucket{le="0.1"} 1.0
# TYPE tradeengine_order_failures_total counter
tradeengine_order_failures_total 0.0
# TYPE tradeengine_risk_rejections_total counter
tradeengine_risk_rejections_total 0.0
# TYPE tradeengine_risk_checks_total counter
tradeengine_risk_checks_total 1.0
# TYPE tradeengine_current_position_size gauge
tradeengine_current_position_size 0.0
# TYPE tradeengine_total_position_value_usd gauge
tradeengine_total_position_value_usd 0.0
# TYPE tradeengine_total_realized_pnl_usd gauge
tradeengine_total_realized_pnl_usd 0.0
# TYPE tradeengine_total_unrealized_pnl_usd gauge
tradeengine_total_unrealized_pnl_usd 0.0
# TYPE tradeengine_total_daily_pnl_usd gauge
tradeengine_total_daily_pnl_usd 0.0
# TYPE tradeengine_order_success_rate gauge
tradeengine_order_success_rate 1.0
"""
        # parse_metrics will parse the actual text, so we don't need to mock it
        # But we need to ensure validate_metrics_present and validate_metric_values return success
        mock_validate_present.return_value = (True, [])
        mock_validate_values.return_value = (True, [])

        # main() doesn't call sys.exit(0) on success - it just returns
        # The test should verify that main() completes without raising SystemExit
        try:
            main()
            # If we get here, main() completed successfully
            assert True
        except SystemExit as e:
            # If main() exits, it should be with code 0 (success)
            assert e.code == 0

    @patch("scripts.validate_business_metrics.fetch_metrics")
    @patch("scripts.validate_business_metrics.parse_metrics")
    @patch("scripts.validate_business_metrics.validate_metrics_present")
    @patch(
        "sys.argv",
        ["validate_business_metrics.py", "--endpoint", "http://localhost:9090/metrics"],
    )
    def test_main_missing_metrics(self, mock_validate_present, mock_parse, mock_fetch):
        """Test main() function when metrics are missing."""
        from scripts.validate_business_metrics import main

        mock_fetch.return_value = "# TYPE test counter\ntest 1.0\n"
        mock_parse.return_value = {
            "test": [{"name": "test", "value": 1.0, "labels": {}}]
        }
        mock_validate_present.return_value = (False, ["missing_metric"])

        # Should exit with code 1 (failure)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch("scripts.validate_business_metrics.fetch_metrics")
    @patch("scripts.validate_business_metrics.parse_metrics")
    @patch("scripts.validate_business_metrics.validate_metrics_present")
    @patch("scripts.validate_business_metrics.validate_metric_values")
    @patch(
        "sys.argv",
        ["validate_business_metrics.py", "--endpoint", "http://localhost:9090/metrics"],
    )
    def test_main_invalid_values(
        self, mock_validate_values, mock_validate_present, mock_parse, mock_fetch
    ):
        """Test main() function when metric values are invalid."""
        from scripts.validate_business_metrics import main

        mock_fetch.return_value = "# TYPE test counter\ntest 1.0\n"
        mock_parse.return_value = {
            "test": [{"name": "test", "value": 1.0, "labels": {}}]
        }
        mock_validate_present.return_value = (True, [])
        mock_validate_values.return_value = (False, ["NaN value detected"])

        # Should exit with code 1 (failure)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
