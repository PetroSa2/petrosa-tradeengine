-- Migration 002: positions composite indexes for open-positions hot path
--
-- Covers get_open_positions(): WHERE status='open' ORDER BY entry_time DESC LIMIT 100
CREATE INDEX IF NOT EXISTS idx_positions_status_entry_time
  ON positions (status, entry_time);

-- Covers upsert_position() + close_position(): WHERE symbol=? AND position_side=? AND status=?
CREATE INDEX IF NOT EXISTS idx_positions_symbol_side_status
  ON positions (symbol, position_side, status);

-- Refresh stats so optimizer picks the new indexes
ANALYZE TABLE positions;
