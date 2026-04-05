-- US Federal Power / Actor-Causality v1 (idempotent)

CREATE TABLE IF NOT EXISTS federal_positions (
  id SERIAL PRIMARY KEY,
  office_title TEXT NOT NULL,
  branch TEXT NOT NULL,
  department TEXT,
  formal_power_weight DOUBLE PRECISION NOT NULL,
  is_senate_confirmed_role BOOLEAN NOT NULL DEFAULT FALSE,
  order_rank INTEGER NOT NULL DEFAULT 100,
  summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_federal_positions_title_branch_dept
ON federal_positions (office_title, branch, (COALESCE(department, '')));

CREATE TABLE IF NOT EXISTS federal_actors (
  id SERIAL PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  office_title TEXT,
  branch TEXT,
  department TEXT,
  country_code TEXT NOT NULL DEFAULT 'us' REFERENCES countries (code) ON DELETE RESTRICT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  formal_power_score DOUBLE PRECISION,
  political_influence_score DOUBLE PRECISION,
  x_signal_score DOUBLE PRECISION,
  conflict_index DOUBLE PRECISION,
  agenda_alignment_score DOUBLE PRECISION,
  power_status TEXT,
  summary TEXT,
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_federal_actors_country ON federal_actors (country_code);
CREATE INDEX IF NOT EXISTS idx_federal_actors_active ON federal_actors (is_active);
CREATE INDEX IF NOT EXISTS idx_federal_actors_formal ON federal_actors (formal_power_score DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS actor_position_history (
  id SERIAL PRIMARY KEY,
  actor_id INTEGER NOT NULL REFERENCES federal_actors (id) ON DELETE CASCADE,
  position_id INTEGER NOT NULL REFERENCES federal_positions (id) ON DELETE RESTRICT,
  start_date DATE NOT NULL,
  end_date DATE,
  status TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_name TEXT,
  source_url TEXT,
  confidence TEXT NOT NULL DEFAULT 'confirmed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actor_position_history_actor ON actor_position_history (actor_id);
CREATE INDEX IF NOT EXISTS idx_actor_position_history_dates ON actor_position_history (start_date DESC);

CREATE TABLE IF NOT EXISTS federal_events (
  id SERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  event_date DATE NOT NULL,
  branch TEXT,
  department TEXT,
  impact_direction TEXT,
  impact_strength INTEGER,
  source_type TEXT NOT NULL,
  source_name TEXT,
  source_url TEXT,
  confidence TEXT NOT NULL DEFAULT 'confirmed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_federal_events_date ON federal_events (event_date DESC);

CREATE TABLE IF NOT EXISTS federal_event_actors (
  id SERIAL PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES federal_events (id) ON DELETE CASCADE,
  actor_id INTEGER NOT NULL REFERENCES federal_actors (id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  stance TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (event_id, actor_id, role)
);

CREATE INDEX IF NOT EXISTS idx_federal_event_actors_event ON federal_event_actors (event_id);
CREATE INDEX IF NOT EXISTS idx_federal_event_actors_actor ON federal_event_actors (actor_id);

CREATE TABLE IF NOT EXISTS federal_event_consequences (
  id SERIAL PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES federal_events (id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_actor_id INTEGER REFERENCES federal_actors (id) ON DELETE SET NULL,
  target_department TEXT,
  dimension TEXT NOT NULL,
  impact_value DOUBLE PRECISION NOT NULL,
  time_horizon TEXT NOT NULL DEFAULT 'immediate',
  confidence TEXT NOT NULL DEFAULT 'confirmed',
  explanation TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_federal_event_consequences_event ON federal_event_consequences (event_id);

CREATE TABLE IF NOT EXISTS federal_timeline_snapshots (
  id SERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  overall_power_stability DOUBLE PRECISION,
  executive_cohesion DOUBLE PRECISION,
  cabinet_stability DOUBLE PRECISION,
  legislative_alignment DOUBLE PRECISION,
  conflict_temperature DOUBLE PRECISION,
  narrative_pressure DOUBLE PRECISION,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_federal_timeline_snapshots_date ON federal_timeline_snapshots (snapshot_date DESC);

CREATE TABLE IF NOT EXISTS federal_ai_cache (
  id SERIAL PRIMARY KEY,
  cache_key TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
