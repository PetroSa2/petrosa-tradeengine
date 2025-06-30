#!/usr/bin/env python3
"""
Constants Usage Example - Petrosa Trading Engine

This example demonstrates how to use the new simplified constants system
in your modules and components.

Usage:
    python examples/constants_usage.py
"""

import sys
import os

# Add project root to path so we can import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import constants directly
from shared.constants import (
    # Application
    APP_NAME, APP_VERSION, ENVIRONMENT, DEBUG,
    
    # Database
    MONGODB_URL, MONGODB_DATABASE,
    
    # Messaging
    NATS_SERVERS, NATS_SIGNAL_SUBJECT,
    
    # API
    API_HOST, API_PORT, API_RELOAD,
    
    # Trading
    DEFAULT_BASE_AMOUNT, SIMULATION_ENABLED, SUPPORTED_SYMBOLS,
    
    # Exchange
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET,
    
    # Monitoring
    LOG_LEVEL, PROMETHEUS_ENABLED,
    
    # Enums
    Environment, LogLevel, OrderType, OrderSide,
    
    # Utility functions
    get_config_summary, validate_configuration
)


def demonstrate_constants_usage():
    """Demonstrate how to use the constants in your code"""
    
    print("=" * 60)
    print("Petrosa Trading Engine - Constants Usage Example")
    print("=" * 60)
    
    # 1. Basic application info
    print(f"\n1. Application Information:")
    print(f"   Name: {APP_NAME}")
    print(f"   Version: {APP_VERSION}")
    print(f"   Environment: {ENVIRONMENT}")
    print(f"   Debug Mode: {DEBUG}")
    
    # 2. Database configuration
    print(f"\n2. Database Configuration:")
    print(f"   MongoDB URL: {MONGODB_URL}")
    print(f"   MongoDB Database: {MONGODB_DATABASE}")
    
    # 3. Messaging configuration
    print(f"\n3. Messaging Configuration:")
    print(f"   NATS Servers: {NATS_SERVERS}")
    print(f"   NATS Signal Subject: {NATS_SIGNAL_SUBJECT}")
    
    # 4. API configuration
    print(f"\n4. API Configuration:")
    print(f"   Host: {API_HOST}")
    print(f"   Port: {API_PORT}")
    print(f"   Auto-reload: {API_RELOAD}")
    
    # 5. Trading configuration
    print(f"\n5. Trading Configuration:")
    print(f"   Default Base Amount: {DEFAULT_BASE_AMOUNT}")
    print(f"   Simulation Enabled: {SIMULATION_ENABLED}")
    print(f"   Supported Symbols: {SUPPORTED_SYMBOLS}")
    
    # 6. Exchange configuration
    print(f"\n6. Exchange Configuration:")
    print(f"   Binance Testnet: {BINANCE_TESTNET}")
    print(f"   Binance API Key Set: {bool(BINANCE_API_KEY)}")
    print(f"   Binance API Secret Set: {bool(BINANCE_API_SECRET)}")
    
    # 7. Monitoring configuration
    print(f"\n7. Monitoring Configuration:")
    print(f"   Log Level: {LOG_LEVEL}")
    print(f"   Prometheus Enabled: {PROMETHEUS_ENABLED}")
    
    # 8. Using enums
    print(f"\n8. Using Enums:")
    print(f"   Environment Types: {[env.value for env in Environment]}")
    print(f"   Log Levels: {[level.value for level in LogLevel]}")
    print(f"   Order Types: {[order_type.value for order_type in OrderType]}")
    print(f"   Order Sides: {[side.value for side in OrderSide]}")
    
    # 9. Configuration summary
    print(f"\n9. Configuration Summary:")
    config_summary = get_config_summary()
    for section, config in config_summary.items():
        print(f"   {section}: {config}")
    
    # 10. Configuration validation
    print(f"\n10. Configuration Validation:")
    issues = validate_configuration()
    if issues:
        print("   Issues found:")
        for issue in issues:
            print(f"     - {issue}")
    else:
        print("   No configuration issues found!")
    
    # 11. Environment-specific behavior
    print(f"\n11. Environment-Specific Behavior:")
    if ENVIRONMENT == Environment.PRODUCTION:
        print("   Running in PRODUCTION mode")
        print("   - Simulation disabled")
        print("   - Auto-reload disabled")
        print("   - Log level set to WARNING")
    elif ENVIRONMENT == Environment.STAGING:
        print("   Running in STAGING mode")
        print("   - Simulation enabled for safety")
        print("   - Auto-reload disabled")
    elif ENVIRONMENT == Environment.TESTING:
        print("   Running in TESTING mode")
        print("   - Simulation enabled")
        print("   - Auto-reload enabled")
        print("   - Debug logging enabled")
    else:
        print("   Running in DEVELOPMENT mode")
        print("   - Simulation enabled")
        print("   - Auto-reload enabled")
        print("   - Debug mode enabled")


def demonstrate_conditional_logic():
    """Demonstrate conditional logic using constants"""
    
    print(f"\n" + "=" * 60)
    print("Conditional Logic Examples")
    print("=" * 60)
    
    # Example 1: Environment-based configuration
    if ENVIRONMENT == Environment.PRODUCTION:
        print("Production: Using live trading with strict risk management")
    elif ENVIRONMENT == Environment.STAGING:
        print("Staging: Using simulation with production-like settings")
    else:
        print("Development: Using simulation with relaxed settings")
    
    # Example 2: Feature flags
    if SIMULATION_ENABLED:
        print("Trading: Using simulation mode")
    else:
        print("Trading: Using live trading mode")
    
    # Example 3: Logging levels
    if LOG_LEVEL == LogLevel.DEBUG:
        print("Logging: Verbose debug logging enabled")
    elif LOG_LEVEL == LogLevel.INFO:
        print("Logging: Standard info logging")
    else:
        print("Logging: Minimal logging for production")
    
    # Example 4: Exchange configuration
    if BINANCE_TESTNET:
        print("Binance: Using testnet (safe for testing)")
    else:
        print("Binance: Using live trading (real money!)")
    
    # Example 5: API configuration
    if API_RELOAD:
        print("API: Auto-reload enabled (development mode)")
    else:
        print("API: Auto-reload disabled (production mode)")


def demonstrate_validation():
    """Demonstrate configuration validation"""
    
    print(f"\n" + "=" * 60)
    print("Configuration Validation")
    print("=" * 60)
    
    issues = validate_configuration()
    
    if issues:
        print("Configuration issues found:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        
        print("\nTo fix these issues:")
        print("1. Set the required environment variables")
        print("2. Check your .env file")
        print("3. Ensure production secrets are properly configured")
    else:
        print("✅ All configuration is valid!")
        print("Your Petrosa Trading Engine is ready to run.")


if __name__ == "__main__":
    try:
        demonstrate_constants_usage()
        demonstrate_conditional_logic()
        demonstrate_validation()
        
        print(f"\n" + "=" * 60)
        print("Constants Usage Example Complete!")
        print("=" * 60)
        print("\nKey takeaways:")
        print("1. Import constants directly: from shared.constants import API_HOST")
        print("2. Use enums for type safety: Environment.PRODUCTION")
        print("3. Use utility functions: get_config_summary(), validate_configuration()")
        print("4. Environment variables override defaults automatically")
        print("5. Environment-specific overrides are applied automatically")
        
    except Exception as e:
        print(f"Error running constants example: {e}")
        sys.exit(1) 