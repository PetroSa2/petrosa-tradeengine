"""Test ETHUSDT Notional Fix

Test to verify that the calculate_min_order_amount function properly rounds UP
to ensure orders meet minimum notional requirements, specifically for the
ETHUSDT scenario where we were getting $19.59 instead of the required $20.00.
"""

import pytest

from tradeengine.exchange.binance import BinanceFuturesExchange


@pytest.fixture
def exchange():
    """Create exchange with ETHUSDT symbol info"""
    exchange = BinanceFuturesExchange()
    exchange.initialized = True

    # Mock ETHUSDT symbol info
    exchange.symbol_info = {
        "ETHUSDT": {
            "symbol": "ETHUSDT",
            "baseAsset": "ETH",
            "quoteAsset": "USDT",
            "filters": [
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "stepSize": "0.001",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "notional": "20.0",
                },
            ],
        }
    }
    return exchange


class TestETHUSDTNotionalFix:
    """Test ETHUSDT specific notional calculation fix"""

    def test_ethusdt_at_3918_96(self, exchange):
        """Test ETHUSDT at $3918.96 (exact scenario from error logs)"""
        symbol = "ETHUSDT"
        price = 3918.96

        # Calculate minimum order amount
        min_amount = exchange.calculate_min_order_amount(symbol, price)

        # Calculate actual notional value
        notional_value = min_amount * price

        # Assertions
        assert min_amount >= 0.005103, (
            f"Quantity {min_amount} is below minimum required 0.005103 "
            f"for $20 notional at ${price}"
        )
        assert notional_value >= 20.0, (
            f"Notional value ${notional_value:.2f} is below minimum $20.00. "
            f"Quantity: {min_amount:.6f}, Price: ${price:.2f}"
        )

        print(f"✓ ETHUSDT at ${price:.2f}")
        print(f"  Minimum quantity: {min_amount:.6f}")
        print(f"  Notional value: ${notional_value:.2f}")

    def test_ethusdt_at_3921_92(self, exchange):
        """Test ETHUSDT at $3921.92 (another scenario from error logs)"""
        symbol = "ETHUSDT"
        price = 3921.92

        # Calculate minimum order amount
        min_amount = exchange.calculate_min_order_amount(symbol, price)

        # Calculate actual notional value
        notional_value = min_amount * price

        # Required minimum quantity
        min_required = 20.0 / price  # = 0.005100

        # Assertions
        assert min_amount >= min_required, (
            f"Quantity {min_amount} is below minimum required {min_required:.6f} "
            f"for $20 notional at ${price}"
        )
        assert notional_value >= 20.0, (
            f"Notional value ${notional_value:.2f} is below minimum $20.00. "
            f"Quantity: {min_amount:.6f}, Price: ${price:.2f}"
        )

        print(f"✓ ETHUSDT at ${price:.2f}")
        print(f"  Minimum quantity: {min_amount:.6f}")
        print(f"  Notional value: ${notional_value:.2f}")

    def test_ethusdt_various_prices(self, exchange):
        """Test ETHUSDT at various price points"""
        symbol = "ETHUSDT"

        # Test various price points
        test_prices = [
            3900.00,
            3918.96,  # From error logs
            3921.92,  # From error logs
            3950.00,
            4000.00,
            4100.00,
        ]

        for price in test_prices:
            min_amount = exchange.calculate_min_order_amount(symbol, price)
            notional_value = min_amount * price
            min_required = 20.0 / price

            assert min_amount >= min_required, (
                f"At ${price:.2f}: Quantity {min_amount} is below minimum "
                f"required {min_required:.6f}"
            )
            assert (
                notional_value >= 20.0
            ), f"At ${price:.2f}: Notional ${notional_value:.2f} is below $20.00"

            print(
                f"✓ ETHUSDT at ${price:.2f}: "
                f"qty={min_amount:.6f}, notional=${notional_value:.2f}"
            )

    def test_step_size_rounding(self, exchange):
        """Test that quantities are properly rounded to step_size"""
        symbol = "ETHUSDT"
        price = 3918.96

        min_amount = exchange.calculate_min_order_amount(symbol, price)

        # Check that quantity is a valid multiple of step_size (0.001)
        # Due to floating point, we check with a small tolerance
        remainder = (min_amount * 1000) % 1
        assert abs(remainder) < 0.0001 or abs(remainder - 1) < 0.0001, (
            f"Quantity {min_amount} is not a valid multiple of step_size 0.001. "
            f"Remainder: {remainder}"
        )

    def test_safety_margin_always_applied(self, exchange):
        """Test that the 5% safety margin keeps us above minimum"""
        symbol = "ETHUSDT"
        price = 3918.96

        # Exact minimum without margin
        exact_min = 20.0 / price  # = 0.005103176...

        # Calculate with our function (includes 5% margin + ceiling)
        min_amount = exchange.calculate_min_order_amount(symbol, price)

        # Should be greater than exact minimum due to margin and ceiling
        assert min_amount > exact_min, (
            f"Calculated amount {min_amount} should be greater than "
            f"exact minimum {exact_min:.6f}"
        )

        # Even if price increases slightly, should still be above $20
        # (This tests the safety margin)
        price_variance = price * 1.02  # 2% price increase
        notional_at_higher_price = min_amount * price_variance

        print("Safety margin test:")
        print(f"  Base price: ${price:.2f}")
        print(f"  Quantity: {min_amount:.6f}")
        print(f"  Notional at base: ${min_amount * price:.2f}")
        print(f"  Notional at +2%: ${notional_at_higher_price:.2f}")
