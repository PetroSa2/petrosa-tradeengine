#!/usr/bin/env python3
"""
Example script demonstrating the usage of constants in the trading engine.
This shows how to use the various enums and constants defined in the system.
"""

from typing import Any

from contracts.order import OrderSide, OrderStatus, OrderType
from contracts.signal import SignalType


def demonstrate_signal_types() -> None:
    """Demonstrate different signal types."""
    print("=== Signal Types ===")

    signal_types = [SignalType.BUY, SignalType.SELL, SignalType.HOLD]

    for signal_type in signal_types:
        print(f"Signal Type: {signal_type.value}")
        print(f"  Description: {signal_type.name}")
        print()


def demonstrate_order_types() -> None:
    """Demonstrate different order types."""
    print("=== Order Types ===")

    order_types = [
        OrderType.MARKET,
        OrderType.LIMIT,
        OrderType.STOP,
        OrderType.STOP_LIMIT,
        OrderType.TAKE_PROFIT,
        OrderType.TAKE_PROFIT_LIMIT,
    ]

    for order_type in order_types:
        print(f"Order Type: {order_type.value}")
        print(f"  Description: {order_type.name}")
        print()


def demonstrate_order_sides() -> None:
    """Demonstrate different order sides."""
    print("=== Order Sides ===")

    order_sides = [OrderSide.BUY, OrderSide.SELL]

    for order_side in order_sides:
        print(f"Order Side: {order_side.value}")
        print(f"  Description: {order_side.name}")
        print()


def demonstrate_order_statuses() -> None:
    """Demonstrate different order statuses."""
    print("=== Order Statuses ===")

    order_statuses = [
        OrderStatus.PENDING,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.PARTIALLY_FILLED,
    ]

    for order_status in order_statuses:
        print(f"Order Status: {order_status.value}")
        print(f"  Description: {order_status.name}")
        print()


def create_order_example() -> dict[str, Any]:
    """Create an example order using constants."""
    print("=== Creating Order Example ===")

    order_data = {
        "symbol": "BTCUSDT",
        "order_type": OrderType.MARKET.value,
        "side": OrderSide.BUY.value,
        "quantity": 0.1,
        "price": 45000.0,
        "status": OrderStatus.PENDING.value,
        "time_in_force": "GTC",
        "position_size_pct": 0.1,
    }

    print("Order Data:")
    for key, value in order_data.items():
        print(f"  {key}: {value}")
    print()

    return order_data


def create_signal_example() -> dict[str, Any]:
    """Create an example signal using constants."""
    print("=== Creating Signal Example ===")

    signal_data = {
        "id": "example-signal-001",
        "symbol": "BTCUSDT",
        "signal_type": SignalType.BUY.value,
        "price": 45000.0,
        "quantity": 0.1,
        "timestamp": 1640995200,
        "source": "example_script",
        "confidence": 0.8,
        "metadata": {"strategy": "momentum", "timeframe": "1h"},
        "timeframe": "1h",
        "strategy": "momentum_strategy",
    }

    print("Signal Data:")
    for key, value in signal_data.items():
        print(f"  {key}: {value}")
    print()

    return signal_data


def demonstrate_validation() -> None:
    """Demonstrate validation using constants."""
    print("=== Validation Examples ===")

    # Valid signal type
    valid_signal_type = SignalType.BUY
    print(f"Valid signal type: {valid_signal_type.value}")

    # Valid order type
    valid_order_type = OrderType.MARKET
    print(f"Valid order type: {valid_order_type.value}")

    # Valid order side
    valid_order_side = OrderSide.BUY
    print(f"Valid order side: {valid_order_side.value}")

    # Valid order status
    valid_order_status = OrderStatus.PENDING
    print(f"Valid order status: {valid_order_status.value}")
    print()


def demonstrate_comparison() -> None:
    """Demonstrate comparing constants."""
    print("=== Comparison Examples ===")

    # Compare signal types
    signal_type1 = SignalType.BUY
    signal_type2 = SignalType.SELL

    print(f"Signal type 1: {signal_type1.value}")
    print(f"Signal type 2: {signal_type2.value}")
    print(f"Are they equal? {signal_type1 == signal_type2}")
    print()

    # Compare order types
    order_type1 = OrderType.MARKET
    order_type2 = OrderType.LIMIT

    print(f"Order type 1: {order_type1.value}")
    print(f"Order type 2: {order_type2.value}")
    print(f"Are they equal? {order_type1 == order_type2}")
    print()


def demonstrate_iteration() -> None:
    """Demonstrate iterating through constants."""
    print("=== Iteration Examples ===")

    print("All Signal Types:")
    for signal_type in SignalType:
        print(f"  {signal_type.name}: {signal_type.value}")
    print()

    print("All Order Types:")
    for order_type in OrderType:
        print(f"  {order_type.name}: {order_type.value}")
    print()

    print("All Order Sides:")
    for order_side in OrderSide:
        print(f"  {order_side.name}: {order_side.value}")
    print()

    print("All Order Statuses:")
    for order_status in OrderStatus:
        print(f"  {order_status.name}: {order_status.value}")
    print()


def main() -> None:
    """Main function to demonstrate constants usage."""
    print("Petrosa Trading Engine - Constants Usage Example")
    print("=" * 50)
    print()

    # Demonstrate all constant types
    demonstrate_signal_types()
    demonstrate_order_types()
    demonstrate_order_sides()
    demonstrate_order_statuses()

    # Create examples
    create_order_example()
    create_signal_example()

    # Demonstrate validation and comparison
    demonstrate_validation()
    demonstrate_comparison()
    demonstrate_iteration()

    print("Constants usage example completed!")


if __name__ == "__main__":
    main()
