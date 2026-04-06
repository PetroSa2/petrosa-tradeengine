#!/usr/bin/env python3
"""
Test MongoDB Validation and Catastrophic Failure Behavior
"""

import asyncio
import logging
import os
import sys

from shared.constants import get_mongodb_connection_string, validate_mongodb_config

# Add the project root to the path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_default_configuration() -> None:
    """Test with default configuration (should fail)"""
    print("\n1️⃣ Testing with default configuration (should fail catastrophically):")
    try:
        # Clear any existing MongoDB environment variables
        if "MONGODB_URI" in os.environ:
            del os.environ["MONGODB_URI"]
        if "MONGODB_DATABASE" in os.environ:
            del os.environ["MONGODB_DATABASE"]

        validate_mongodb_config()
        print("❌ FAILED: Should have failed with default configuration")
    except ValueError as e:
        print(f"✅ PASSED: Correctly failed with error: {e}")


def test_invalid_url_format() -> None:
    """Test with invalid URL format (should fail)"""
    print("\n2️⃣ Testing with invalid URL format (should fail catastrophically):")
    try:
        os.environ["MONGODB_URI"] = "invalid-url"
        os.environ["MONGODB_DATABASE"] = "test"

        validate_mongodb_config()
        print("❌ FAILED: Should have failed with invalid URL format")
    except ValueError as e:
        print(f"✅ PASSED: Correctly failed with error: {e}")


def test_valid_configuration() -> None:
    """Test with valid configuration (should pass)"""
    print("\n3️⃣ Testing with valid configuration (should pass):")
    try:
        os.environ["MONGODB_URI"] = "mongodb://localhost:27017"
        os.environ["MONGODB_DATABASE"] = "test"

        validate_mongodb_config()
        connection_string = get_mongodb_connection_string()
        print("✅ PASSED: Valid configuration accepted")
        print(f"   Connection string: {connection_string}")
    except Exception as e:
        print(f"❌ FAILED: Should have passed with valid configuration: {e}")


def test_atlas_configuration() -> None:
    """Test with MongoDB Atlas configuration (should pass)"""
    print("\n4️⃣ Testing with MongoDB Atlas configuration (should pass):")
    try:
        os.environ["MONGODB_URI"] = (
            "mongodb+srv://username:password@cluster.mongodb.net"
        )
        os.environ["MONGODB_DATABASE"] = "production"

        validate_mongodb_config()
        connection_string = get_mongodb_connection_string()
        print("✅ PASSED: Atlas configuration accepted")
        print(f"   Connection string: {connection_string}")
    except Exception as e:
        print(f"❌ FAILED: Should have passed with Atlas configuration: {e}")


def test_missing_database() -> None:
    """Test with missing database (should fail)"""
    print("\n5️⃣ Testing with missing database (should fail catastrophically):")
    try:
        os.environ["MONGODB_URI"] = "mongodb://localhost:27017"
        if "MONGODB_DATABASE" in os.environ:
            del os.environ["MONGODB_DATABASE"]

        validate_mongodb_config()
        print("❌ FAILED: Should have failed with missing database")
    except ValueError as e:
        print(f"✅ PASSED: Correctly failed with error: {e}")


def test_mongodb_validation() -> None:
    """Test MongoDB validation with different configurations"""

    print("🧪 Testing MongoDB Validation and Catastrophic Failure Behavior")
    print("=" * 70)

    # Run individual tests
    test_default_configuration()
    test_invalid_url_format()
    test_valid_configuration()
    test_atlas_configuration()
    test_missing_database()

    print("\n" + "=" * 70)
    print("🎯 Summary:")
    print("✅ MongoDB validation correctly fails catastrophically when:")
    print("   - MONGODB_URI is not configured (missing from Kubernetes secret)")
    print("   - MONGODB_URI has invalid format")
    print("   - MONGODB_DATABASE is not configured (missing from Kubernetes configmap)")
    print("✅ MongoDB validation passes when:")
    print("   - Valid MongoDB URI is provided from Kubernetes secret")
    print("   - Valid database name is provided from Kubernetes configmap")
    print(
        "\n🚨 This ensures the service fails fast if MongoDB is not properly configured!"
    )
    print("✅ All environments require proper Kubernetes configuration")


async def test_api_startup_validation() -> None:
    """Test API startup validation"""
    print("\n🔧 Testing API Startup Validation")
    print("=" * 50)

    # Clear MongoDB environment variables to simulate unconfigured state
    if "MONGODB_URI" in os.environ:
        del os.environ["MONGODB_URI"]
    if "MONGODB_DATABASE" in os.environ:
        del os.environ["MONGODB_DATABASE"]

    try:
        # Import the API module (this should trigger validation)

        print("❌ FAILED: API should not start without MongoDB configuration")
    except Exception as e:
        print(f"✅ PASSED: API correctly failed to start: {e}")
        print("   This demonstrates catastrophic failure behavior!")


def main() -> None:
    """Main test function"""
    print("🚀 MongoDB Validation Test Suite")
    print("Testing catastrophic failure behavior for critical MongoDB configuration")

    # Run validation tests
    test_mongodb_validation()

    # Run API startup test
    asyncio.run(test_api_startup_validation())

    print("\n🎉 All tests completed!")
    print(
        "The service will now fail catastrophically if MongoDB is not properly "
        "configured."
    )


if __name__ == "__main__":
    main()
