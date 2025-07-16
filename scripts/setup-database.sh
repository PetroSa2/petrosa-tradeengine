#!/bin/bash

# Petrosa Trading Engine - Database Setup Script
# This script sets up the MySQL database schema for distributed state management

set -e

echo "🚀 Setting up Petrosa Trading Engine database schema..."

# Check if MySQL client is available
if ! command -v mysql &> /dev/null; then
    echo "❌ MySQL client is not installed. Please install mysql-client."
    exit 1
fi

# Database configuration
DB_HOST="${MYSQL_HOST:-localhost}"
DB_PORT="${MYSQL_PORT:-3306}"
DB_USER="${MYSQL_USER:-root}"
DB_PASSWORD="${MYSQL_PASSWORD:-}"
DB_NAME="${MYSQL_DATABASE:-petrosa}"

echo "📊 Database Configuration:"
echo "  Host: $DB_HOST"
echo "  Port: $DB_PORT"
echo "  User: $DB_USER"
echo "  Database: $DB_NAME"

# Function to run MySQL command
run_mysql() {
    if [ -n "$DB_PASSWORD" ]; then
        mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" -p"$DB_PASSWORD" "$@"
    else
        mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$@"
    fi
}

# Test database connection
echo "🔍 Testing database connection..."
if ! run_mysql -e "SELECT 1;" &> /dev/null; then
    echo "❌ Failed to connect to MySQL database"
    echo "Please check your database configuration:"
    echo "  - MYSQL_HOST: $DB_HOST"
    echo "  - MYSQL_PORT: $DB_PORT"
    echo "  - MYSQL_USER: $DB_USER"
    echo "  - MYSQL_PASSWORD: [set]"
    echo "  - MYSQL_DATABASE: $DB_NAME"
    exit 1
fi

echo "✅ Database connection successful"

# Create database if it doesn't exist
echo "📦 Creating database if it doesn't exist..."
run_mysql -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\`;"

# Run the schema creation script
echo "🏗️  Creating database schema..."
run_mysql "$DB_NAME" < scripts/create-database-schema.sql

echo "✅ Database schema created successfully"

# Verify tables were created
echo "🔍 Verifying table creation..."
run_mysql "$DB_NAME" -e "
SHOW TABLES;
"

echo "📊 Table structure verification:"
run_mysql "$DB_NAME" -e "
DESCRIBE positions;
DESCRIBE daily_pnl;
DESCRIBE position_history;
DESCRIBE distributed_locks;
DESCRIBE leader_election;
DESCRIBE distributed_config;
"

# Test stored procedures
echo "🧪 Testing stored procedures..."
run_mysql "$DB_NAME" -e "
CALL CleanupExpiredLocks();
SELECT 'Stored procedures working correctly' as status;
"

# Insert initial daily P&L record
echo "📝 Creating initial daily P&L record..."
run_mysql "$DB_NAME" -e "
INSERT IGNORE INTO daily_pnl (date, daily_pnl) VALUES (CURDATE(), 0.0);
"

echo "✅ Database setup completed successfully!"
echo ""
echo "🎉 Petrosa Trading Engine database is ready for distributed state management."
echo ""
echo "📋 Next steps:"
echo "  1. Update your .env file with the correct MySQL connection details"
echo "  2. Restart your trading engine pods"
echo "  3. Check the health endpoint: curl http://localhost:8000/health"
echo ""
echo "🔧 Database connection string:"
echo "   mysql://$DB_USER:****@$DB_HOST:$DB_PORT/$DB_NAME"
