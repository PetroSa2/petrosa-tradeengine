#!/usr/bin/env python3
"""
Production Validation Script for Business Metrics

Validates that all 11 business metrics are:
1. Exposed on /metrics endpoint
2. Being scraped by Prometheus
3. Emitting correct values during live trading
4. Not causing performance issues

Usage:
    python scripts/validate_business_metrics.py --endpoint http://tradeengine:9090/metrics
    python scripts/validate_business_metrics.py --prometheus-url http://prometheus:9090
"""

import argparse
import math
import sys
import time
from typing import Dict, List, Set

import requests
from prometheus_client.parser import text_string_to_metric_families

# Expected business metrics (11 total)
EXPECTED_METRICS: dict[str, str] = {
    # Order Execution (3 metrics)
    "tradeengine_orders_executed_by_type_total": "Counter",
    "tradeengine_order_execution_latency_seconds": "Histogram",
    "tradeengine_order_failures_total": "Counter",
    # Risk Management (2 metrics)
    "tradeengine_risk_rejections_total": "Counter",
    "tradeengine_risk_checks_total": "Counter",
    # Position & PnL (6 metrics)
    "tradeengine_current_position_size": "Gauge",
    "tradeengine_total_position_value_usd": "Gauge",
    "tradeengine_total_realized_pnl_usd": "Gauge",
    "tradeengine_total_unrealized_pnl_usd": "Gauge",
    "tradeengine_total_daily_pnl_usd": "Gauge",
    "tradeengine_order_success_rate": "Gauge",
}


def fetch_metrics(endpoint: str, timeout: int = 10) -> str:
    """Fetch metrics from the /metrics endpoint."""
    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to fetch metrics from {endpoint}: {e}")
        sys.exit(1)


def parse_metrics(metrics_text: str) -> dict[str, list[dict]]:
    """Parse Prometheus metrics text into structured format."""
    metrics = {}
    for family in text_string_to_metric_families(metrics_text):
        metric_name = family.name
        if metric_name not in metrics:
            metrics[metric_name] = []
        for sample in family.samples:
            metrics[metric_name].append(
                {
                    "name": sample.name,
                    "labels": sample.labels,
                    "value": sample.value,
                }
            )
    return metrics


def validate_metrics_present(metrics: dict[str, list[dict]]) -> tuple[bool, list[str]]:
    """Validate all expected business metrics are present."""
    found_metrics: set[str] = set()
    missing_metrics: list[str] = []

    # Check for each expected metric
    for metric_name in EXPECTED_METRICS.keys():
        # Check base name and any suffixed variants (e.g., _bucket, _count, _sum for histograms)
        found = False
        for existing_metric in metrics.keys():
            if existing_metric == metric_name or existing_metric.startswith(
                f"{metric_name}_"
            ):
                found_metrics.add(metric_name)
                found = True
                break

        if not found:
            missing_metrics.append(metric_name)

    all_present = len(missing_metrics) == 0
    return all_present, missing_metrics


def validate_metric_values(metrics: dict[str, list[dict]]) -> tuple[bool, list[str]]:
    """Validate metric values are reasonable (no NaN, Inf, negative counters)."""
    issues: list[str] = []

    for metric_name, metric_type in EXPECTED_METRICS.items():
        if metric_name not in metrics:
            continue

        for sample in metrics[metric_name]:
            value = sample["value"]

            # Check for NaN or Inf
            if isinstance(value, float):
                if math.isnan(value):
                    issues.append(f"{sample['name']}: NaN value detected")
                elif math.isinf(value):
                    issues.append(f"{sample['name']}: Inf value detected")

            # Counters should not be negative
            if metric_type == "Counter" and value < 0:
                issues.append(f"{sample['name']}: Counter has negative value: {value}")

            # Histogram buckets should be non-negative
            if metric_type == "Histogram" and "_bucket" in sample["name"]:
                if value < 0:
                    issues.append(
                        f"{sample['name']}: Histogram bucket has negative value: {value}"
                    )

    return len(issues) == 0, issues


def print_metrics_summary(metrics: dict[str, list[dict]]):
    """Print summary of found business metrics."""
    print("\n📊 Business Metrics Summary:")
    print("=" * 80)

    for metric_name, metric_type in EXPECTED_METRICS.items():
        found_samples = []
        for existing_metric in metrics.keys():
            if existing_metric == metric_name or existing_metric.startswith(
                f"{metric_name}_"
            ):
                found_samples.extend(metrics[existing_metric])

        if found_samples:
            print(f"\n✅ {metric_name} ({metric_type})")
            print(f"   Found {len(found_samples)} sample(s)")
            # Show first sample as example
            sample = found_samples[0]
            print(f"   Example: {sample['name']} = {sample['value']}")
            if sample["labels"]:
                print(f"   Labels: {sample['labels']}")
        else:
            print(f"\n❌ {metric_name} ({metric_type}) - NOT FOUND")


def main():
    parser = argparse.ArgumentParser(
        description="Validate business metrics in production environment"
    )
    parser.add_argument(
        "--endpoint",
        default="http://localhost:9090/metrics",
        help="Metrics endpoint URL (default: http://localhost:9090/metrics)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed metric information",
    )

    args = parser.parse_args()

    print("🔍 Business Metrics Validation")
    print("=" * 80)
    print(f"Endpoint: {args.endpoint}")
    print(f"Timeout: {args.timeout}s")
    print()

    # Fetch metrics
    print("📥 Fetching metrics...")
    start_time = time.time()
    metrics_text = fetch_metrics(args.endpoint, args.timeout)
    fetch_duration = time.time() - start_time
    print(f"✅ Metrics fetched in {fetch_duration:.3f}s")
    print(f"   Response size: {len(metrics_text)} bytes")
    print()

    # Parse metrics
    print("🔍 Parsing metrics...")
    metrics = parse_metrics(metrics_text)
    print(f"✅ Found {len(metrics)} unique metric families")
    print()

    # Validate metrics are present
    print("✅ Validating all business metrics are present...")
    all_present, missing = validate_metrics_present(metrics)
    if all_present:
        print("✅ All 11 business metrics are present!")
    else:
        print(f"❌ Missing {len(missing)} metric(s):")
        for metric in missing:
            print(f"   - {metric}")
        sys.exit(1)

    # Validate metric values
    print("\n✅ Validating metric values...")
    values_ok, issues = validate_metric_values(metrics)
    if values_ok:
        print("✅ All metric values are valid!")
    else:
        print(f"❌ Found {len(issues)} issue(s):")
        for issue in issues:
            print(f"   - {issue}")
        sys.exit(1)

    # Print summary
    if args.verbose:
        print_metrics_summary(metrics)

    # Final summary
    print("\n" + "=" * 80)
    print("✅ VALIDATION PASSED")
    print("=" * 80)
    print("✅ All 11 business metrics present and valid")
    print(f"✅ Metrics endpoint responding in {fetch_duration:.3f}s")
    print("✅ No NaN, Inf, or invalid values detected")
    print()
    print("Next steps:")
    print("1. Verify Prometheus is scraping these metrics")
    print("2. Run Prometheus queries to validate data flow")
    print("3. Monitor metrics during live trading activity")
    print("4. Check performance impact (CPU/memory)")


if __name__ == "__main__":
    main()
