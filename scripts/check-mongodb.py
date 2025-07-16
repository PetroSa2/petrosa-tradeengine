#!/usr/bin/env python3
"""
MongoDB Status Check Script for Petrosa Trading Engine
"""

import asyncio
import os
import sys

try:
    import motor.motor_asyncio
except ImportError:
    print("❌ motor package not installed. Run 'pip install motor'")
    sys.exit(1)


async def check_mongodb_basic() -> bool:
    """Basic MongoDB connection check"""
    try:
        # Import constants for MongoDB configuration
        sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
        from shared.constants import MONGODB_DATABASE, MONGODB_URI

        mongodb_uri = os.getenv("MONGODB_URI", f"{MONGODB_URI}/{MONGODB_DATABASE}")
        client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_uri)
        await client.admin.command("ping")
        print("✅ MongoDB is accessible")

        # Check collections
        db = client.petrosa
        collections = await db.list_collection_names()
        print(f"📊 Collections: {collections}")

        # Check indexes
        for collection_name in [
            "positions",
            "daily_pnl",
            "distributed_locks",
            "leader_election",
        ]:
            if collection_name in collections:
                indexes = await db[collection_name].list_indexes().to_list(None)
                print(f"📋 {collection_name} indexes: {len(indexes)}")

        client.close()
        return True
    except Exception as e:
        print(f"❌ MongoDB error: {e}")
        return False


async def check_mongodb_detailed() -> bool:
    """Detailed MongoDB health check"""
    try:
        # Import constants for MongoDB configuration
        sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
        from shared.constants import MONGODB_DATABASE, MONGODB_URI

        mongodb_uri = os.getenv("MONGODB_URI", f"{MONGODB_URI}/{MONGODB_DATABASE}")
        client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_uri)
        await client.admin.command("ping")
        print("✅ MongoDB connection successful")

        db = client.petrosa

        # Check all required collections
        required_collections = [
            "positions",
            "daily_pnl",
            "distributed_locks",
            "leader_election",
            "audit_logs",
        ]
        for collection_name in required_collections:
            try:
                count = await db[collection_name].count_documents({})
                print(f"📊 {collection_name}: {count} documents")
            except Exception as e:
                print(f"❌ {collection_name}: Error - {e}")

        # Check distributed state
        positions = await db.positions.find({"status": "open"}).to_list(None)
        print(f"💰 Open positions: {len(positions)}")

        leader = await db.leader_election.find_one({"status": "leader"})
        if leader:
            print(f'👑 Current leader: {leader.get("pod_id", "unknown")}')
        else:
            print("👑 No leader elected")

        client.close()
        return True
    except Exception as e:
        print(f"❌ MongoDB error: {e}")
        return False


def main() -> None:
    """Main function"""
    if len(sys.argv) > 1 and sys.argv[1] == "detailed":
        success = asyncio.run(check_mongodb_detailed())
    else:
        success = asyncio.run(check_mongodb_basic())

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
