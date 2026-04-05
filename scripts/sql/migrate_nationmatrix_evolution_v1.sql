-- StateVerge Evolution Engine v1 — full schema (idempotent) (historical filename: migrate_nationmatrix_evolution_v1.sql)

CREATE TABLE IF NOT EXISTS actors (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  country_code TEXT NOT NULL REFERENCES countries (code) ON DELETE CASCADE,
  summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actors_country ON actors (country_code);

CREATE TABLE IF NOT EXISTS event_actors (
  id SERIAL PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
  actor_id INTEGER NOT NULL REFERENCES actors (id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  UNIQUE (event_id, actor_id, role)
);

CREATE INDEX IF NOT EXISTS idx_event_actors_event ON event_actors (event_id);

CREATE TABLE IF NOT EXISTS event_consequences (
  id SERIAL PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
  target_country_code TEXT NOT NULL REFERENCES countries (code) ON DELETE CASCADE,
  dimension TEXT NOT NULL,
  impact_value DOUBLE PRECISION NOT NULL,
  time_horizon TEXT NOT NULL DEFAULT 'immediate',
  confidence INTEGER NOT NULL DEFAULT 70,
  explanation TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_event_consequences_event ON event_consequences (event_id);
CREATE INDEX IF NOT EXISTS idx_event_consequences_country ON event_consequences (target_country_code);

CREATE TABLE IF NOT EXISTS ai_insights_cache (
  id SERIAL PRIMARY KEY,
  cache_key TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE events ADD COLUMN IF NOT EXISTS applied_to_scores BOOLEAN DEFAULT FALSE;
ALTER TABLE events ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
ALTER TABLE events ALTER COLUMN applied_to_scores SET DEFAULT FALSE;

ALTER TABLE country_scores ADD COLUMN IF NOT EXISTS power_score DOUBLE PRECISION;
ALTER TABLE country_scores ADD COLUMN IF NOT EXISTS power_classification TEXT;

ALTER TABLE country_score_snapshots ADD COLUMN IF NOT EXISTS power_score DOUBLE PRECISION;
ALTER TABLE country_score_snapshots ADD COLUMN IF NOT EXISTS power_delta DOUBLE PRECISION;
ALTER TABLE country_score_snapshots ADD COLUMN IF NOT EXISTS power_trend TEXT;
ALTER TABLE country_score_snapshots ADD COLUMN IF NOT EXISTS power_note TEXT;
