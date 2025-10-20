"""
MongoDB indexes for trading configuration collections.

This script creates the necessary collections and indexes for the trading
configuration system in MongoDB.

Usage:
    python scripts/migrations/001_trading_configs_mongodb.py
"""

import asyncio
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_trading_config_indexes():
    """Create MongoDB collections and indexes for trading configurations."""

    # Get MongoDB connection string
    mongodb_uri = os.getenv("MONGODB_URI")
    mongodb_database = os.getenv("MONGODB_DATABASE", "petrosa")

    if not mongodb_uri:
        raise ValueError("MONGODB_URI environment variable is required")

    logger.info(f"Connecting to MongoDB: {mongodb_database}")
    client = AsyncIOMotorClient(mongodb_uri)
    db = client[mongodb_database]

    try:
        # =====================================================================
        # Global Trading Configurations
        # =====================================================================
        logger.info("Creating trading_configs_global collection and indexes...")
        global_collection = db["trading_configs_global"]

        await global_collection.create_index("updated_at", name="idx_updated_at")
        await global_collection.create_index("created_at", name="idx_created_at")

        logger.info("✓ Global config indexes created")

        # =====================================================================
        # Symbol-Specific Trading Configurations
        # =====================================================================
        logger.info("Creating trading_configs_symbol collection and indexes...")
        symbol_collection = db["trading_configs_symbol"]

        await symbol_collection.create_index(
            "symbol", unique=True, name="unique_symbol"
        )
        await symbol_collection.create_index("symbol", name="idx_symbol")
        await symbol_collection.create_index("updated_at", name="idx_updated_at")

        logger.info("✓ Symbol config indexes created")

        # =====================================================================
        # Symbol-Side Trading Configurations
        # =====================================================================
        logger.info("Creating trading_configs_symbol_side collection and indexes...")
        symbol_side_collection = db["trading_configs_symbol_side"]

        await symbol_side_collection.create_index(
            [("symbol", 1), ("side", 1)], unique=True, name="unique_symbol_side"
        )
        await symbol_side_collection.create_index("symbol", name="idx_symbol")
        await symbol_side_collection.create_index("side", name="idx_side")
        await symbol_side_collection.create_index("updated_at", name="idx_updated_at")

        logger.info("✓ Symbol-side config indexes created")

        # =====================================================================
        # Trading Configuration Audit Trail
        # =====================================================================
        logger.info("Creating trading_configs_audit collection and indexes...")
        audit_collection = db["trading_configs_audit"]

        await audit_collection.create_index("config_type", name="idx_config_type")
        await audit_collection.create_index("symbol", name="idx_symbol")
        await audit_collection.create_index("side", name="idx_side")
        await audit_collection.create_index("changed_by", name="idx_changed_by")
        await audit_collection.create_index("timestamp", name="idx_timestamp")
        await audit_collection.create_index("action", name="idx_action")
        await audit_collection.create_index(
            [("symbol", 1), ("side", 1), ("timestamp", -1)],
            name="idx_symbol_side_timestamp",
        )

        logger.info("✓ Audit trail indexes created")

        # =====================================================================
        # Leverage Status Tracking
        # =====================================================================
        logger.info("Creating leverage_status collection and indexes...")
        leverage_collection = db["leverage_status"]

        await leverage_collection.create_index(
            "symbol", unique=True, name="unique_symbol"
        )
        await leverage_collection.create_index("last_sync_at", name="idx_last_sync_at")
        await leverage_collection.create_index("updated_at", name="idx_updated_at")

        logger.info("✓ Leverage status indexes created")

        # =====================================================================
        # Verify Collections
        # =====================================================================
        collections = await db.list_collection_names()
        logger.info(f"\nVerifying collections: {collections}")

        expected_collections = [
            "trading_configs_global",
            "trading_configs_symbol",
            "trading_configs_symbol_side",
            "trading_configs_audit",
            "leverage_status",
        ]

        for collection_name in expected_collections:
            if collection_name in collections:
                logger.info(f"✓ {collection_name} exists")
                indexes = await db[collection_name].index_information()
                logger.info(f"  Indexes: {list(indexes.keys())}")
            else:
                logger.warning(f"✗ {collection_name} not found")

        logger.info("\n✅ MongoDB migration completed successfully")

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(create_trading_config_indexes())
