-- Petrosa Trading Engine - Database Schema for Distributed State Management
-- This script creates the necessary tables for position persistence across multiple pods

-- Create database if it doesn't exist
CREATE DATABASE IF NOT EXISTS petrosa;
USE petrosa;

-- Positions table for distributed state management
CREATE TABLE IF NOT EXISTS positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    quantity DECIMAL(20,8) NOT NULL DEFAULT 0,
    avg_price DECIMAL(20,8) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(20,8) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(20,8) NOT NULL DEFAULT 0,
    total_cost DECIMAL(20,8) NOT NULL DEFAULT 0,
    total_value DECIMAL(20,8) NOT NULL DEFAULT 0,
    entry_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status ENUM('open', 'closed') NOT NULL DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_symbol (symbol),
    INDEX idx_status (status),
    INDEX idx_last_update (last_update),
    UNIQUE KEY unique_open_symbol (symbol, status)
);

-- Daily P&L tracking
CREATE TABLE IF NOT EXISTS daily_pnl (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    daily_pnl DECIMAL(20,8) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_date (date),
    INDEX idx_date (date)
);

-- Position history for audit trail
CREATE TABLE IF NOT EXISTS position_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    action ENUM('open', 'update', 'close') NOT NULL,
    quantity DECIMAL(20,8) NOT NULL,
    avg_price DECIMAL(20,8) NOT NULL,
    unrealized_pnl DECIMAL(20,8) NOT NULL,
    realized_pnl DECIMAL(20,8) NOT NULL,
    total_cost DECIMAL(20,8) NOT NULL,
    total_value DECIMAL(20,8) NOT NULL,
    order_id VARCHAR(100),
    order_side ENUM('buy', 'sell'),
    fill_price DECIMAL(20,8),
    fill_quantity DECIMAL(20,8),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_timestamp (symbol, timestamp),
    INDEX idx_order_id (order_id),
    INDEX idx_timestamp (timestamp)
);

-- Distributed lock table for coordination
CREATE TABLE IF NOT EXISTS distributed_locks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    lock_name VARCHAR(100) NOT NULL,
    pod_id VARCHAR(100) NOT NULL,
    acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_lock_name (lock_name),
    INDEX idx_expires_at (expires_at),
    INDEX idx_pod_id (pod_id)
);

-- Leader election table
CREATE TABLE IF NOT EXISTS leader_election (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    pod_id VARCHAR(100) NOT NULL,
    elected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status ENUM('leader', 'candidate', 'follower') NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_leader (status),
    INDEX idx_pod_id (pod_id),
    INDEX idx_last_heartbeat (last_heartbeat)
);

-- Configuration table for distributed settings
CREATE TABLE IF NOT EXISTS distributed_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    config_key VARCHAR(100) NOT NULL,
    config_value TEXT,
    updated_by VARCHAR(100),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_config_key (config_key),
    INDEX idx_updated_at (updated_at)
);

-- Insert initial configuration
INSERT IGNORE INTO distributed_config (config_key, config_value, updated_by) VALUES
('max_position_size_pct', '0.1', 'system'),
('max_daily_loss_pct', '0.05', 'system'),
('max_portfolio_exposure_pct', '0.8', 'system'),
('sync_interval_seconds', '30', 'system'),
('lock_timeout_seconds', '60', 'system'),
('heartbeat_interval_seconds', '10', 'system');

-- Create views for easier querying
CREATE OR REPLACE VIEW open_positions AS
SELECT 
    symbol,
    quantity,
    avg_price,
    unrealized_pnl,
    realized_pnl,
    total_cost,
    total_value,
    entry_time,
    last_update
FROM positions 
WHERE status = 'open' AND quantity > 0;

CREATE OR REPLACE VIEW portfolio_summary AS
SELECT 
    COUNT(*) as total_positions,
    SUM(quantity * avg_price) as total_exposure,
    SUM(unrealized_pnl) as total_unrealized_pnl,
    SUM(realized_pnl) as total_realized_pnl,
    MAX(last_update) as last_position_update
FROM positions 
WHERE status = 'open';

-- Create stored procedures for common operations
DELIMITER //

CREATE PROCEDURE IF NOT EXISTS AcquireLock(
    IN p_lock_name VARCHAR(100),
    IN p_pod_id VARCHAR(100),
    IN p_timeout_seconds INT
)
BEGIN
    DECLARE lock_expires TIMESTAMP;
    SET lock_expires = DATE_ADD(NOW(), INTERVAL p_timeout_seconds SECOND);
    
    INSERT INTO distributed_locks (lock_name, pod_id, expires_at)
    VALUES (p_lock_name, p_pod_id, lock_expires)
    ON DUPLICATE KEY UPDATE
        pod_id = CASE 
            WHEN expires_at < NOW() THEN p_pod_id
            ELSE pod_id
        END,
        expires_at = CASE 
            WHEN expires_at < NOW() THEN lock_expires
            ELSE expires_at
        END;
    
    SELECT ROW_COUNT() as acquired;
END //

CREATE PROCEDURE IF NOT EXISTS ReleaseLock(
    IN p_lock_name VARCHAR(100),
    IN p_pod_id VARCHAR(100)
)
BEGIN
    DELETE FROM distributed_locks 
    WHERE lock_name = p_lock_name AND pod_id = p_pod_id;
END //

CREATE PROCEDURE IF NOT EXISTS CleanupExpiredLocks()
BEGIN
    DELETE FROM distributed_locks WHERE expires_at < NOW();
END //

CREATE PROCEDURE IF NOT EXISTS ElectLeader(
    IN p_pod_id VARCHAR(100)
)
BEGIN
    DECLARE current_leader VARCHAR(100);
    DECLARE leader_heartbeat TIMESTAMP;
    
    -- Get current leader info
    SELECT pod_id, last_heartbeat INTO current_leader, leader_heartbeat
    FROM leader_election 
    WHERE status = 'leader' 
    LIMIT 1;
    
    -- If no leader or leader is stale, try to become leader
    IF current_leader IS NULL OR leader_heartbeat < DATE_SUB(NOW(), INTERVAL 30 SECOND) THEN
        INSERT INTO leader_election (pod_id, status, last_heartbeat)
        VALUES (p_pod_id, 'leader', NOW())
        ON DUPLICATE KEY UPDATE
            pod_id = CASE 
                WHEN last_heartbeat < DATE_SUB(NOW(), INTERVAL 30 SECOND) THEN p_pod_id
                ELSE pod_id
            END,
            last_heartbeat = CASE 
                WHEN last_heartbeat < DATE_SUB(NOW(), INTERVAL 30 SECOND) THEN NOW()
                ELSE last_heartbeat
            END;
    END IF;
    
    -- Return current leader
    SELECT pod_id, status, last_heartbeat
    FROM leader_election 
    WHERE status = 'leader';
END //

CREATE PROCEDURE IF NOT EXISTS UpdateHeartbeat(
    IN p_pod_id VARCHAR(100)
)
BEGIN
    INSERT INTO leader_election (pod_id, status, last_heartbeat)
    VALUES (p_pod_id, 'leader', NOW())
    ON DUPLICATE KEY UPDATE
        last_heartbeat = NOW();
END //

DELIMITER ;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_positions_symbol_status ON positions(symbol, status);
CREATE INDEX IF NOT EXISTS idx_positions_last_update ON positions(last_update);
CREATE INDEX IF NOT EXISTS idx_daily_pnl_date ON daily_pnl(date);
CREATE INDEX IF NOT EXISTS idx_position_history_symbol_timestamp ON position_history(symbol, timestamp);

-- Insert sample data for testing (optional)
-- INSERT INTO daily_pnl (date, daily_pnl) VALUES (CURDATE(), 0.0);

-- Show created tables
SHOW TABLES;

-- Show table structure
DESCRIBE positions;
DESCRIBE daily_pnl;
DESCRIBE position_history;
DESCRIBE distributed_locks;
DESCRIBE leader_election;
DESCRIBE distributed_config; 