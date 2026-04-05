-- Evolution Engine v2: federal event chains, snapshot trends, apply flags (idempotent)

ALTER TABLE federal_events
  ADD COLUMN IF NOT EXISTS parent_event_id INTEGER REFERENCES federal_events (id) ON DELETE SET NULL;

ALTER TABLE federal_events
  ADD COLUMN IF NOT EXISTS applied_to_scores BOOLEAN DEFAULT FALSE;

ALTER TABLE federal_events
  ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_federal_events_parent ON federal_events (parent_event_id);
CREATE INDEX IF NOT EXISTS idx_federal_events_applied ON federal_events (applied_to_scores);

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS aggregate_formal_power DOUBLE PRECISION;

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS aggregate_influence DOUBLE PRECISION;

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS aggregate_conflict DOUBLE PRECISION;

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS power_trend_extended TEXT;

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS conflict_trend TEXT;

ALTER TABLE federal_timeline_snapshots
  ADD COLUMN IF NOT EXISTS influence_trend TEXT;
