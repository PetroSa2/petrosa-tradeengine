-- Strategy Positions Table
-- Tracks virtual strategy positions with their own TP/SL
-- Each signal creates a strategy position that can close independently

CREATE TABLE IF NOT EXISTS strategy_positions (
    -- Identification
    strategy_position_id VARCHAR(255) PRIMARY KEY,
    strategy_id VARCHAR(255) NOT NULL,
    signal_id VARCHAR(255),

    -- Position Details
    symbol VARCHAR(50) NOT NULL,
    side ENUM('LONG', 'SHORT') NOT NULL,

    -- Entry Details
    entry_quantity DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    entry_time DATETIME NOT NULL,
    entry_order_id VARCHAR(255),

    -- Strategy's Own TP/SL Targets
    take_profit_price DECIMAL(20, 8),
    stop_loss_price DECIMAL(20, 8),
    tp_order_id VARCHAR(255),
    sl_order_id VARCHAR(255),

    -- Exit Details (when THIS strategy's TP/SL triggers)
    status ENUM('open', 'closed', 'partial') NOT NULL DEFAULT 'open',
    exit_quantity DECIMAL(20, 8),
    exit_price DECIMAL(20, 8),
    exit_time DATETIME,
    exit_order_id VARCHAR(255),
    close_reason ENUM('take_profit', 'stop_loss', 'manual', 'partial', 'liquidation'),

    -- PnL for THIS strategy
    realized_pnl DECIMAL(20, 8),
    realized_pnl_pct DECIMAL(10, 4),
    commission_total DECIMAL(20, 8) DEFAULT 0,

    -- Link to Exchange Position
    exchange_position_key VARCHAR(255),  -- symbol_LONG or symbol_SHORT

    -- Strategy Metadata
    strategy_metadata JSON,

    -- Timestamps
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes for Performance
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_symbol (symbol),
    INDEX idx_status (status),
    INDEX idx_exchange_position (exchange_position_key),
    INDEX idx_created_at (created_at),
    INDEX idx_strategy_symbol_status (strategy_id, symbol, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Position Contributions Table
-- Links strategy positions to their contributions to exchange positions
-- Used for tracking which strategies contributed to aggregated positions

CREATE TABLE IF NOT EXISTS position_contributions (
    -- Identification
    contribution_id VARCHAR(255) PRIMARY KEY,

    -- Links
    strategy_position_id VARCHAR(255) NOT NULL,
    exchange_position_key VARCHAR(255) NOT NULL,  -- symbol_LONG or symbol_SHORT

    -- Contribution Details
    strategy_id VARCHAR(255) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    position_side ENUM('LONG', 'SHORT') NOT NULL,

    -- Contribution Amount
    contribution_quantity DECIMAL(20, 8) NOT NULL,
    contribution_entry_price DECIMAL(20, 8) NOT NULL,
    contribution_time DATETIME NOT NULL,

    -- Position State When Contribution Made
    position_sequence INT,  -- 1st, 2nd, 3rd contribution to this exchange position
    exchange_quantity_before DECIMAL(20, 8),  -- Exchange position qty before this contribution
    exchange_quantity_after DECIMAL(20, 8),   -- Exchange position qty after this contribution

    -- Closure Tracking
    status ENUM('active', 'closed') NOT NULL DEFAULT 'active',
    close_reason VARCHAR(50),
    exit_time DATETIME,
    exit_price DECIMAL(20, 8),

    -- Attribution of Profit
    contribution_pnl DECIMAL(20, 8),
    contribution_pnl_pct DECIMAL(10, 4),

    -- Metadata
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_strategy_position (strategy_position_id),
    INDEX idx_exchange_position (exchange_position_key),
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_symbol_side (symbol, position_side),
    INDEX idx_status (status),

    -- Foreign Keys
    FOREIGN KEY (strategy_position_id) REFERENCES strategy_positions(strategy_position_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Exchange Positions Table
-- Tracks the actual exchange (Binance) position state
-- This is the aggregated reality on the exchange

CREATE TABLE IF NOT EXISTS exchange_positions (
    -- Identification
    exchange_position_key VARCHAR(255) PRIMARY KEY,  -- BTCUSDT_LONG

    -- Position Details
    symbol VARCHAR(50) NOT NULL,
    side ENUM('LONG', 'SHORT') NOT NULL,

    -- Current State on Exchange
    current_quantity DECIMAL(20, 8) NOT NULL DEFAULT 0,
    weighted_avg_price DECIMAL(20, 8) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,

    -- Lifecycle
    first_entry_time DATETIME,
    last_update_time DATETIME NOT NULL,
    status ENUM('open', 'closed') NOT NULL DEFAULT 'open',

    -- Contributing Strategies
    contributing_strategies JSON,  -- List of strategy IDs that contributed
    total_contributions INT DEFAULT 0,

    -- Metadata
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_symbol_side (symbol, side),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Views for Analytics

-- Strategy Performance View
CREATE OR REPLACE VIEW strategy_performance AS
SELECT
    strategy_id,
    COUNT(*) as total_positions,
    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_positions,
    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_positions,
    SUM(CASE WHEN close_reason = 'take_profit' THEN 1 ELSE 0 END) as tp_hits,
    SUM(CASE WHEN close_reason = 'stop_loss' THEN 1 ELSE 0 END) as sl_hits,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
    AVG(realized_pnl) as avg_pnl,
    SUM(realized_pnl) as total_pnl,
    AVG(realized_pnl_pct) as avg_pnl_pct,
    AVG(TIMESTAMPDIFF(SECOND, entry_time, exit_time)) as avg_duration_seconds
FROM strategy_positions
WHERE status = 'closed'
GROUP BY strategy_id;

-- Contribution Summary View
CREATE OR REPLACE VIEW contribution_summary AS
SELECT
    pc.strategy_id,
    pc.symbol,
    pc.position_side,
    COUNT(*) as total_contributions,
    SUM(pc.contribution_quantity) as total_quantity_contributed,
    AVG(pc.contribution_entry_price) as avg_entry_price,
    SUM(pc.contribution_pnl) as total_pnl_from_contributions,
    AVG(pc.contribution_pnl_pct) as avg_pnl_pct
FROM position_contributions pc
WHERE pc.status = 'closed'
GROUP BY pc.strategy_id, pc.symbol, pc.position_side;
