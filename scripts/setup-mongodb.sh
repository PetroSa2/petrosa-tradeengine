#!/bin/bash

# Petrosa Trading Engine - MongoDB Setup Script
# This script sets up MongoDB for distributed state management

set -e

echo "🚀 Setting up MongoDB for Petrosa Trading Engine..."

# MongoDB configuration
MONGODB_HOST="${MONGODB_HOST:-localhost}"
MONGODB_PORT="${MONGODB_PORT:-27017}"
MONGODB_USER="${MONGODB_USER:-}"
MONGODB_PASSWORD="${MONGODB_PASSWORD:-}"
MONGODB_DATABASE="${MONGODB_DATABASE:-petrosa}"

echo "📊 MongoDB Configuration:"
echo "  Host: $MONGODB_HOST"
echo "  Port: $MONGODB_PORT"
echo "  Database: $MONGODB_DATABASE"
echo "  User: $MONGODB_USER"

# Function to run MongoDB command
run_mongodb() {
    if [ -n "$MONGODB_USER" ] && [ -n "$MONGODB_PASSWORD" ]; then
        mongosh --host "$MONGODB_HOST" --port "$MONGODB_PORT" --username "$MONGODB_USER" --password "$MONGODB_PASSWORD" --authenticationDatabase admin "$@"
    else
        mongosh --host "$MONGODB_HOST" --port "$MONGODB_PORT" "$@"
    fi
}

# Check if MongoDB is running
echo "🔍 Checking MongoDB connection..."
if ! run_mongodb --eval "db.adminCommand('ping')" > /dev/null 2>&1; then
    echo "❌ MongoDB is not running or not accessible"
    echo "Please start MongoDB and ensure it's accessible at $MONGODB_HOST:$MONGODB_PORT"
    exit 1
fi

echo "✅ MongoDB is accessible"

# Create database and collections
echo "📝 Creating database and collections..."

# Create the database and collections
run_mongodb --eval "
// Create database
use $MONGODB_DATABASE;

// Create collections with proper indexes
db.createCollection('positions');
db.createCollection('daily_pnl');
db.createCollection('distributed_locks');
db.createCollection('leader_election');
db.createCollection('audit_logs');

// Create indexes for better performance
db.positions.createIndex({symbol: 1}, {unique: true});
db.positions.createIndex({status: 1});
db.positions.createIndex({updated_at: 1});

db.daily_pnl.createIndex({date: 1}, {unique: true});
db.daily_pnl.createIndex({updated_at: 1});

db.distributed_locks.createIndex({lock_name: 1}, {unique: true});
db.distributed_locks.createIndex({expires_at: 1});
db.distributed_locks.createIndex({pod_id: 1});

db.leader_election.createIndex({status: 1}, {unique: true});
db.leader_election.createIndex({pod_id: 1});
db.leader_election.createIndex({last_heartbeat: 1});

db.audit_logs.createIndex({timestamp: 1});
db.audit_logs.createIndex({level: 1});
db.audit_logs.createIndex({component: 1});

print('Database and collections created successfully');
"

# Create user if credentials are provided
if [ -n "$MONGODB_USER" ] && [ -n "$MONGODB_PASSWORD" ]; then
    echo "👤 Creating MongoDB user..."
    run_mongodb admin --eval "
    // Create user for the database
    db.createUser({
        user: '$MONGODB_USER',
        pwd: '$MONGODB_PASSWORD',
        roles: [
            {role: 'readWrite', db: '$MONGODB_DATABASE'},
            {role: 'dbAdmin', db: '$MONGODB_DATABASE'}
        ]
    });
    print('User created successfully');
    "
fi

# Test the setup
echo "🧪 Testing MongoDB setup..."

# Test positions collection
run_mongodb --eval "
use $MONGODB_DATABASE;
db.positions.insertOne({
    symbol: 'TEST',
    quantity: 0,
    avg_price: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    total_cost: 0,
    total_value: 0,
    entry_time: new Date(),
    last_update: new Date(),
    status: 'open',
    updated_at: new Date()
});
db.positions.deleteOne({symbol: 'TEST'});
print('Positions collection test: PASSED');
"

# Test daily P&L collection
run_mongodb --eval "
use $MONGODB_DATABASE;
db.daily_pnl.insertOne({
    date: new Date().toISOString().split('T')[0],
    daily_pnl: 0,
    updated_at: new Date()
});
db.daily_pnl.deleteOne({date: new Date().toISOString().split('T')[0]});
print('Daily P&L collection test: PASSED');
"

# Test distributed locks collection
run_mongodb --eval "
use $MONGODB_DATABASE;
db.distributed_locks.insertOne({
    lock_name: 'test_lock',
    pod_id: 'test_pod',
    acquired_at: new Date(),
    expires_at: new Date(Date.now() + 60000),
    updated_at: new Date()
});
db.distributed_locks.deleteOne({lock_name: 'test_lock'});
print('Distributed locks collection test: PASSED');
"

# Test leader election collection
run_mongodb --eval "
use $MONGODB_DATABASE;
db.leader_election.insertOne({
    pod_id: 'test_leader',
    status: 'leader',
    elected_at: new Date(),
    last_heartbeat: new Date(),
    updated_at: new Date()
});
db.leader_election.deleteOne({pod_id: 'test_leader'});
print('Leader election collection test: PASSED');
"

echo "✅ MongoDB setup completed successfully!"

# Display connection information
echo ""
echo "📋 Connection Information:"
echo "  Database: $MONGODB_DATABASE"
echo "  Host: $MONGODB_HOST:$MONGODB_PORT"

if [ -n "$MONGODB_USER" ]; then
    echo "  User: $MONGODB_USER"
    echo "  Connection String: mongodb://$MONGODB_USER:****@$MONGODB_HOST:$MONGODB_PORT/$MONGODB_DATABASE"
else
    echo "  Connection String: mongodb://$MONGODB_HOST:$MONGODB_PORT/$MONGODB_DATABASE"
fi

echo ""
echo "🔧 Environment Variables:"
echo "  MONGODB_URL=mongodb://$MONGODB_HOST:$MONGODB_PORT/$MONGODB_DATABASE"
echo "  MONGODB_DATABASE=$MONGODB_DATABASE"

if [ -n "$MONGODB_USER" ]; then
    echo "  MONGODB_USER=$MONGODB_USER"
    echo "  MONGODB_PASSWORD=$MONGODB_PASSWORD"
fi

echo ""
echo "🎉 MongoDB is ready for Petrosa Trading Engine!"
echo "You can now start the application with distributed state management."
