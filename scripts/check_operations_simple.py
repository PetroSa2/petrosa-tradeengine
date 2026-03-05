#!/usr/bin/env python3
"""
Simple script to check for operations tables in Petrosa databases
"""

import os
import sys


def check_mysql_operations():
    """Check MySQL database for operations tables"""
    print("🔍 Checking MySQL for operations tables...")

    # Try different MySQL packages
    connection = None
    try:
        # Try PyMySQL first
        import PyMySQL

        print("✅ Using PyMySQL")

        host = os.getenv("MYSQL_HOST", "localhost")
        port = int(os.getenv("MYSQL_PORT", "3306"))
        user = os.getenv("MYSQL_USER", "root")
        password = os.getenv("MYSQL_PASSWORD", "")
        database = os.getenv("MYSQL_DATABASE", "petrosa")

        connection = PyMySQL.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
        )

    except ImportError:
        try:
            import mysql.connector

            print("✅ Using MySQL Connector")

            host = os.getenv("MYSQL_HOST", "localhost")
            port = int(os.getenv("MYSQL_PORT", "3306"))
            user = os.getenv("MYSQL_USER", "root")
            password = os.getenv("MYSQL_PASSWORD", "")
            database = os.getenv("MYSQL_DATABASE", "petrosa")

            connection = mysql.connector.connect(
                host=host, port=port, user=user, password=password, database=database
            )

        except ImportError:
            print("❌ No MySQL packages available")
            return False

    if not connection:
        print("❌ Failed to establish database connection")
        return False

    try:
        cursor = connection.cursor()

        # Get all tables
        cursor.execute("SHOW TABLES;")
        tables = cursor.fetchall()
        print(f"📊 Found {len(tables)} tables in database {database}:")

        operations_found = False
        all_tables = []

        for table_row in tables:
            table_name = table_row[0]
            all_tables.append(table_name)

            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`;")
            count = cursor.fetchone()[0]

            # Check for operations-related tables
            if "operation" in table_name.lower():
                print(f"🎯 OPERATIONS TABLE: {table_name} - {count} rows")
                operations_found = True

                # Show table structure
                cursor.execute(f"DESCRIBE `{table_name}`;")
                columns = cursor.fetchall()
                print(f"    📝 Columns: {[col[0] for col in columns]}")

                if count > 0:
                    cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 3;")
                    samples = cursor.fetchall()
                    print(f'    📋 Sample data: {samples[0] if samples else "No data"}')
            else:
                print(f"  - {table_name}: {count} rows")

        # Look for tables that might contain operational data
        operational_tables = [
            t
            for t in all_tables
            if any(
                keyword in t.lower()
                for keyword in ["history", "audit", "log", "event", "trade"]
            )
        ]

        if operational_tables:
            print("\n📋 TABLES THAT MIGHT CONTAIN OPERATIONAL DATA:")
            for table_name in operational_tables:
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`;")
                count = cursor.fetchone()[0]
                print(f"  - {table_name}: {count} rows")

                if count > 0:
                    cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 1;")
                    sample = cursor.fetchone()
                    if sample:
                        print("    📋 Has data: ✅")

        cursor.close()
        connection.close()

        return operations_found

    except Exception as e:
        print(f"❌ Database error: {e}")
        if connection:
            connection.close()
        return False


def check_mongodb_operations():
    """Check MongoDB for operations collections"""
    print("🔍 Checking MongoDB for operations collections...")

    try:
        import pymongo

        print("✅ Using PyMongo")

        mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        database_name = os.getenv("MONGODB_DATABASE", "petrosa")

        client = pymongo.MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)

        # Test connection
        client.admin.command("ping")

        db = client[database_name]
        collections = db.list_collection_names()
        print(f"📊 Found {len(collections)} collections in database {database_name}:")

        operations_found = False

        for collection_name in collections:
            count = db[collection_name].count_documents({})

            if "operation" in collection_name.lower():
                print(
                    f"🎯 OPERATIONS COLLECTION: {collection_name} - {count} documents"
                )
                operations_found = True

                if count > 0:
                    sample = db[collection_name].find_one()
                    print(
                        f'    📋 Sample keys: {list(sample.keys()) if sample else "No data"}'
                    )
            else:
                print(f"  - {collection_name}: {count} documents")

        # Check operational collections
        operational_collections = [
            c
            for c in collections
            if any(
                keyword in c.lower()
                for keyword in ["history", "audit", "log", "event", "trade"]
            )
        ]

        if operational_collections:
            print("\n📋 COLLECTIONS THAT MIGHT CONTAIN OPERATIONAL DATA:")
            for collection_name in operational_collections:
                count = db[collection_name].count_documents({})
                print(f"  - {collection_name}: {count} documents")

                if count > 0:
                    sample = db[collection_name].find_one()
                    print("    📋 Has data: ✅")

        client.close()
        return operations_found

    except ImportError:
        print("❌ PyMongo not available")
        return False
    except Exception as e:
        print(f"❌ MongoDB error: {e}")
        return False


def main():
    """Main function"""
    print("🔍 PETROSA OPERATIONS TABLE CHECK")
    print("=" * 50)

    print("\n1. MYSQL CHECK:")
    mysql_ops = check_mysql_operations()

    print("\n2. MONGODB CHECK:")
    mongodb_ops = check_mongodb_operations()

    print("\n" + "=" * 50)
    print("📋 SUMMARY:")
    print(f"  MySQL operations tables: {'✅ Found' if mysql_ops else '❌ Not found'}")
    print(
        f"  MongoDB operations collections: {'✅ Found' if mongodb_ops else '❌ Not found'}"
    )

    if not mysql_ops and not mongodb_ops:
        print("\n🤔 No dedicated operations tables found.")
        print("   Operations data might be stored in:")
        print("   - position_history table (trading operations)")
        print("   - audit_logs collection (audit operations)")
        print("   - Application logs (operational events)")

    print("\n💡 To check database connectivity:")
    print("   export MYSQL_HOST=your_mysql_host")
    print("   export MYSQL_USER=your_mysql_user")
    print("   export MYSQL_PASSWORD=your_mysql_password")
    print("   export MONGODB_URI=mongodb://your_mongo_host:27017")


if __name__ == "__main__":
    main()
