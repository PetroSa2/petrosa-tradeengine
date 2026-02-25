#!/usr/bin/env python3
"""
Script to check for operations tables in Petrosa databases
"""

import os
import sys


def check_mysql_operations():
    """Check MySQL database for operations tables"""
    try:
        import PyMySQL as pymysql

        print("✅ PyMySQL available")
    except ImportError:
        try:
            import mysql.connector

            print("✅ MySQL Connector available")
            # We'll use mysql.connector instead
        except ImportError:
            print("❌ No MySQL packages available - cannot check MySQL tables")
            return False

    # Database connection parameters
    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "petrosa")

    print(f"🔍 Checking MySQL database: {database} on {host}:{port}")

    try:
        # Connect to database
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
        )

        with connection.cursor() as cursor:
            # Check if database exists and get all tables
            cursor.execute("SHOW TABLES;")
            tables = cursor.fetchall()
            print(f"📊 Found {len(tables)} tables in database:")

            operations_found = False

            for table in tables:
                table_name = table[0]

                # Get row count for each table
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`;")
                count = cursor.fetchone()[0]

                # Check if it's an operations-related table
                if "operation" in table_name.lower():
                    print(f"🎯 OPERATIONS TABLE: {table_name} - {count} rows")
                    operations_found = True

                    # Get sample data
                    cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 3;")
                    sample = cursor.fetchall()
                    if sample:
                        print(f"    📋 Sample data: {sample[0]}")
                    else:
                        print("    📋 No data in operations table")

                    # Show table structure
                    cursor.execute(f"DESCRIBE `{table_name}`;")
                    columns = cursor.fetchall()
                    print(f"    📝 Columns: {[col[0] for col in columns]}")
                else:
                    print(f"  - {table_name}: {count} rows")

            # Specifically look for operations tables by pattern
            cursor.execute("SHOW TABLES LIKE '%operation%';")
            operations_tables = cursor.fetchall()

            if operations_tables:
                print(f"\n🎯 OPERATIONS TABLES FOUND: {len(operations_tables)}")
                for table in operations_tables:
                    table_name = table[0]
                    cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`;")
                    count = cursor.fetchone()[0]
                    print(f"  - {table_name}: {count} rows")
                    operations_found = True

            # Check related tables that might contain operational data
            related_patterns = ["%history%", "%audit%", "%log%", "%event%"]
            for pattern in related_patterns:
                cursor.execute(f"SHOW TABLES LIKE '{pattern}';")
                related_tables = cursor.fetchall()

                if related_tables:
                    print(f'\n📋 {pattern.replace("%", "").upper()} TABLES:')
                    for table in related_tables:
                        table_name = table[0]
                        cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`;")
                        count = cursor.fetchone()[0]
                        print(f"  - {table_name}: {count} rows")

                        # Show columns for these tables
                        cursor.execute(f"DESCRIBE `{table_name}`;")
                        columns = cursor.fetchall()
                        print(f"    Columns: {[col[0] for col in columns]}")

        connection.close()

        if not operations_found:
            print('\n❌ No tables with "operation" in the name found in MySQL')

        return operations_found

    except Exception as e:
        print(f"❌ MySQL error: {e}")
        return False


def check_mongodb_operations():
    """Check MongoDB database for operations collections"""
    try:
        import pymongo

        print("✅ PyMongo available")
    except ImportError:
        print("❌ PyMongo not available - cannot check MongoDB collections")
        return False

    # MongoDB connection parameters
    mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    database_name = os.getenv("MONGODB_DATABASE", "petrosa")

    print(f"🔍 Checking MongoDB database: {database_name} at {mongodb_uri}")

    try:
        client = pymongo.MongoClient(mongodb_uri)
        db = client[database_name]

        # Get all collections
        collections = db.list_collection_names()
        print(f"📊 Found {len(collections)} collections:")

        operations_found = False

        for collection_name in collections:
            count = db[collection_name].count_documents({})

            if "operation" in collection_name.lower():
                print(
                    f"🎯 OPERATIONS COLLECTION: {collection_name} - {count} documents"
                )
                operations_found = True

                # Get sample document
                sample = db[collection_name].find_one()
                if sample:
                    print(f"    📋 Sample document keys: {list(sample.keys())}")
                else:
                    print("    📋 No documents in operations collection")
            else:
                print(f"  - {collection_name}: {count} documents")

        # Check related collections
        related_patterns = ["history", "audit", "log", "event"]
        for pattern in related_patterns:
            related_collections = [c for c in collections if pattern in c.lower()]
            if related_collections:
                print(f"\n📋 {pattern.upper()} COLLECTIONS:")
                for collection_name in related_collections:
                    count = db[collection_name].count_documents({})
                    print(f"  - {collection_name}: {count} documents")

                    # Get sample document structure
                    sample = db[collection_name].find_one()
                    if sample:
                        print(f"    Keys: {list(sample.keys())}")

        client.close()

        if not operations_found:
            print('\n❌ No collections with "operation" in the name found in MongoDB')

        return operations_found

    except Exception as e:
        print(f"❌ MongoDB error: {e}")
        return False


def main():
    """Main function to check both databases"""
    print("🔍 Checking for operations tables in Petrosa databases...\n")

    mysql_operations = check_mysql_operations()
    print("\n" + "=" * 60 + "\n")
    mongodb_operations = check_mongodb_operations()

    print("\n" + "=" * 60)
    print("📋 SUMMARY:")
    print(
        f"  MySQL operations tables: {'✅ Found' if mysql_operations else '❌ Not found'}"
    )
    print(
        f"  MongoDB operations collections: {'✅ Found' if mongodb_operations else '❌ Not found'}"
    )

    if not mysql_operations and not mongodb_operations:
        print("\n🤔 No operations tables/collections found in either database.")
        print("   This could mean:")
        print("   1. Operations data is stored in other tables (like position_history)")
        print("   2. Operations tables haven't been created yet")
        print("   3. Database connection issues")
        print("   4. Different naming convention is used")

    print("\n💡 To populate operations data, you may need to:")
    print("   1. Run trading operations to generate data")
    print("   2. Create operations tables if they don't exist")
    print("   3. Check application logs for operational events")


if __name__ == "__main__":
    main()
