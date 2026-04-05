-- Country-layer evolution: snapshot trends + event parent chain (idempotent)

ALTER TABLE country_score_snapshots
  ADD COLUMN IF NOT EXISTS overall_trend_extended TEXT;

ALTER TABLE country_score_snapshots
  ADD COLUMN IF NOT EXISTS power_trend_extended TEXT;

ALTER TABLE country_score_snapshots
  ADD COLUMN IF NOT EXISTS risk_trend TEXT;

ALTER TABLE country_score_snapshots
  ADD COLUMN IF NOT EXISTS opportunity_trend TEXT;

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS parent_event_id INTEGER REFERENCES events (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_events_parent ON events (parent_event_id);
CREATE INDEX IF NOT EXISTS idx_events_country_date ON events (country_code, event_date);
