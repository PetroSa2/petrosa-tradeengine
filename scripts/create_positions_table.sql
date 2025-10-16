-- Migration script to create positions table for hedge mode tracking
-- Date: 2025-10-16
-- Purpose: Track individual positions with full lifecycle from entry to exit

CREATE TABLE IF NOT EXISTS positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    position_id VARCHAR(255) UNIQUE NOT NULL,
    strategy_id VARCHAR(255) NOT NULL,
    exchange VARCHAR(50) NOT NULL DEFAULT 'binance',
    symbol VARCHAR(50) NOT NULL,
    position_side ENUM('LONG', 'SHORT') NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    entry_time DATETIME NOT NULL,
    stop_loss DECIMAL(20, 8),
    take_profit DECIMAL(20, 8),
    status ENUM('open', 'closed') NOT NULL DEFAULT 'open',
    metadata JSON,
    -- Exchange-specific data
    exchange_position_id VARCHAR(255),
    entry_order_id VARCHAR(255),
    entry_trade_ids JSON,
    stop_loss_order_id VARCHAR(255),
    take_profit_order_id VARCHAR(255),
    commission_asset VARCHAR(20),
    commission_total DECIMAL(20, 8),
    -- Populated on close
    exit_price DECIMAL(20, 8),
    exit_time DATETIME,
    exit_order_id VARCHAR(255),
    exit_trade_ids JSON,
    pnl DECIMAL(20, 8),
    pnl_pct DECIMAL(10, 4),
    pnl_after_fees DECIMAL(20, 8),
    duration_seconds INT,
    close_reason VARCHAR(50),
    final_commission DECIMAL(20, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_exchange (exchange),
    INDEX idx_symbol (symbol),
    INDEX idx_status (status),
    INDEX idx_entry_time (entry_time),
    INDEX idx_exchange_position_id (exchange_position_id),
    INDEX idx_entry_order_id (entry_order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Verify the table was created
SELECT
    TABLE_NAME,
    TABLE_ROWS,
    CREATE_TIME
FROM
    INFORMATION_SCHEMA.TABLES
WHERE
    TABLE_NAME = 'positions';
