-- Event Source Layer v1: country events gain canonical source_type (nullable for legacy rows).
-- Idempotent; no UI change.

ALTER TABLE events ADD COLUMN IF NOT EXISTS source_type TEXT;

COMMENT ON COLUMN events.source_type IS
  'v1 canonical: official|institutional|reputable_media|market_data|x_signal|inferred|ai_generated';
