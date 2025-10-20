-- Migration: Trading Configuration Management Tables
-- Description: Creates tables for storing trading configurations with per-symbol and per-position-side overrides
-- Date: 2025-10-20

-- ============================================================================
-- Global Trading Configurations
-- ============================================================================
CREATE TABLE IF NOT EXISTS trading_configs_global (
    id VARCHAR(36) PRIMARY KEY DEFAULT (UUID()),
    parameters JSON NOT NULL COMMENT 'Trading parameter key-value pairs',
    version INT NOT NULL DEFAULT 1 COMMENT 'Configuration version, incremented on updates',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'When config was first created',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'When config was last updated',
    created_by VARCHAR(100) COMMENT 'Who/what created this config (e.g., llm_agent_v1, admin)',
    metadata JSON COMMENT 'Additional metadata (notes, performance metrics, etc.)',
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Global trading configurations applied to all symbols';

-- ============================================================================
-- Symbol-Specific Trading Configurations (Per-Symbol Overrides)
-- ============================================================================
CREATE TABLE IF NOT EXISTS trading_configs_symbol (
    id VARCHAR(36) PRIMARY KEY DEFAULT (UUID()),
    symbol VARCHAR(20) NOT NULL COMMENT 'Trading symbol (e.g., BTCUSDT)',
    parameters JSON NOT NULL COMMENT 'Trading parameter overrides',
    version INT NOT NULL DEFAULT 1 COMMENT 'Configuration version, incremented on updates',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'When config was first created',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'When config was last updated',
    created_by VARCHAR(100) COMMENT 'Who/what created this config',
    metadata JSON COMMENT 'Additional metadata',
    UNIQUE KEY unique_symbol (symbol),
    INDEX idx_symbol (symbol),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Symbol-specific trading configuration overrides';

-- ============================================================================
-- Symbol-Side Trading Configurations (Per-Symbol-Per-Side Overrides)
-- ============================================================================
CREATE TABLE IF NOT EXISTS trading_configs_symbol_side (
    id VARCHAR(36) PRIMARY KEY DEFAULT (UUID()),
    symbol VARCHAR(20) NOT NULL COMMENT 'Trading symbol (e.g., BTCUSDT)',
    side ENUM('LONG', 'SHORT') NOT NULL COMMENT 'Position side',
    parameters JSON NOT NULL COMMENT 'Trading parameter overrides for this symbol-side combination',
    version INT NOT NULL DEFAULT 1 COMMENT 'Configuration version, incremented on updates',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'When config was first created',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'When config was last updated',
    created_by VARCHAR(100) COMMENT 'Who/what created this config',
    metadata JSON COMMENT 'Additional metadata',
    UNIQUE KEY unique_symbol_side (symbol, side),
    INDEX idx_symbol (symbol),
    INDEX idx_side (side),
    INDEX idx_symbol_side (symbol, side),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Symbol-side-specific trading configuration overrides (e.g., BTCUSDT-LONG)';

-- ============================================================================
-- Trading Configuration Audit Trail
-- ============================================================================
CREATE TABLE IF NOT EXISTS trading_configs_audit (
    id VARCHAR(36) PRIMARY KEY DEFAULT (UUID()),
    config_type ENUM('global', 'symbol', 'symbol_side') NOT NULL COMMENT 'Type of configuration changed',
    symbol VARCHAR(20) COMMENT 'Symbol if applicable',
    side ENUM('LONG', 'SHORT') COMMENT 'Side if applicable',
    action ENUM('create', 'update', 'delete') NOT NULL COMMENT 'Action performed',
    parameters_before JSON COMMENT 'Parameters before change',
    parameters_after JSON COMMENT 'Parameters after change',
    version_before INT COMMENT 'Version before change',
    version_after INT COMMENT 'Version after change',
    changed_by VARCHAR(100) NOT NULL COMMENT 'Who/what made the change',
    reason TEXT COMMENT 'Reason for the change',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'When the change occurred',
    metadata JSON COMMENT 'Additional audit metadata',
    INDEX idx_config_type (config_type),
    INDEX idx_symbol (symbol),
    INDEX idx_side (side),
    INDEX idx_changed_by (changed_by),
    INDEX idx_timestamp (timestamp),
    INDEX idx_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Complete audit trail of all trading configuration changes';

-- ============================================================================
-- Leverage Status Tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS leverage_status (
    id VARCHAR(36) PRIMARY KEY DEFAULT (UUID()),
    symbol VARCHAR(20) NOT NULL COMMENT 'Trading symbol',
    configured_leverage INT NOT NULL COMMENT 'Leverage configured in system',
    actual_leverage INT COMMENT 'Actual leverage on Binance',
    last_sync_at TIMESTAMP COMMENT 'When leverage was last synced',
    last_sync_success BOOLEAN DEFAULT FALSE COMMENT 'Whether last sync was successful',
    last_sync_error TEXT COMMENT 'Error message if sync failed',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_symbol (symbol),
    INDEX idx_symbol (symbol),
    INDEX idx_last_sync_at (last_sync_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Tracks configured vs actual leverage status per symbol';
