-- Migration 002: positions composite indexes for open-positions hot path
--
-- MySQL 5.x does not support CREATE INDEX IF NOT EXISTS.
-- Run the existence checks below first; skip the CREATE INDEX if count > 0.
--
-- Check before running:
--   SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
--    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'positions'
--      AND INDEX_NAME IN ('idx_positions_status_entry_time','idx_positions_symbol_side_status');
--
-- Covers get_open_positions(): WHERE status='open' ORDER BY entry_time DESC LIMIT 100
CREATE INDEX idx_positions_status_entry_time
  ON positions (status, entry_time);

-- Covers upsert_position() + close_position(): WHERE symbol=? AND position_side=? AND status=?
CREATE INDEX idx_positions_symbol_side_status
  ON positions (symbol, position_side, status);

-- Refresh stats so optimizer picks the new indexes
ANALYZE TABLE positions;
