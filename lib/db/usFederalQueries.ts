import { pool } from "@/lib/db";

export type FederalPositionRow = {
  id: number;
  office_title: string;
  branch: string;
  department: string | null;
  formal_power_weight: number;
  is_senate_confirmed_role: boolean;
  order_rank: number;
  summary: string | null;
};

export type FederalActorRow = {
  id: number;
  slug: string;
  name: string;
  actor_type: string;
  office_title: string | null;
  branch: string | null;
  department: string | null;
  is_active: boolean;
  formal_power_score: number | null;
  political_influence_score: number | null;
  x_signal_score: number | null;
  conflict_index: number | null;
  agenda_alignment_score: number | null;
  power_status: string | null;
  summary: string | null;
  last_seen_at: string | null;
};

export type FederalEventRow = {
  id: number;
  event_type: string;
  title: string;
  summary: string | null;
  event_date: string;
  branch: string | null;
  department: string | null;
  impact_direction: string | null;
  impact_strength: number | null;
  source_type: string;
  source_name: string | null;
  source_url: string | null;
  confidence: string;
  created_at: string;
};

export async function getFederalAiCache(
  cacheKey: string
): Promise<Record<string, unknown> | null> {
  try {
    const { rows } = await pool.query(
      `SELECT payload FROM federal_ai_cache WHERE cache_key = $1`,
      [cacheKey]
    );
    return rows[0]?.payload ?? null;
  } catch {
    return null;
  }
}

export async function upsertFederalAiCache(
  cacheKey: string,
  payload: Record<string, unknown>
): Promise<void> {
  await pool.query(
    `
    INSERT INTO federal_ai_cache (cache_key, payload, updated_at)
    VALUES ($1, $2::jsonb, NOW())
    ON CONFLICT (cache_key) DO UPDATE SET
      payload = EXCLUDED.payload,
      updated_at = NOW()
    `,
    [cacheKey, JSON.stringify(payload)]
  );
}

export async function getActorsByFormalPower(limit = 20): Promise<FederalActorRow[]> {
  const { rows } = await pool.query(
    `
    SELECT id, slug, name, actor_type, office_title, branch, department, is_active,
           formal_power_score, political_influence_score, x_signal_score, conflict_index,
           agenda_alignment_score, power_status, summary,
           last_seen_at::text AS last_seen_at
    FROM federal_actors
    WHERE country_code = 'us' AND is_active = TRUE
    ORDER BY formal_power_score DESC NULLS LAST, name ASC
    LIMIT $1
    `,
    [limit]
  );
  return rows as FederalActorRow[];
}

export async function getActorsByInfluence(limit = 20): Promise<FederalActorRow[]> {
  const { rows } = await pool.query(
    `
    SELECT id, slug, name, actor_type, office_title, branch, department, is_active,
           formal_power_score, political_influence_score, x_signal_score, conflict_index,
           agenda_alignment_score, power_status, summary,
           last_seen_at::text AS last_seen_at
    FROM federal_actors
    WHERE country_code = 'us' AND is_active = TRUE
    ORDER BY political_influence_score DESC NULLS LAST, name ASC
    LIMIT $1
    `,
    [limit]
  );
  return rows as FederalActorRow[];
}

export async function getMostContestedActors(limit = 12): Promise<FederalActorRow[]> {
  const { rows } = await pool.query(
    `
    SELECT id, slug, name, actor_type, office_title, branch, department, is_active,
           formal_power_score, political_influence_score, x_signal_score, conflict_index,
           agenda_alignment_score, power_status, summary,
           last_seen_at::text AS last_seen_at
    FROM federal_actors
    WHERE country_code = 'us' AND is_active = TRUE
    ORDER BY conflict_index DESC NULLS LAST, name ASC
    LIMIT $1
    `,
    [limit]
  );
  return rows as FederalActorRow[];
}

export async function getLatestFederalEvents(limit = 15): Promise<FederalEventRow[]> {
  const { rows } = await pool.query(
    `
    SELECT id, event_type, title, summary, event_date::text AS event_date,
           branch, department, impact_direction, impact_strength,
           source_type, source_name, source_url, confidence, created_at::text AS created_at
    FROM federal_events
    ORDER BY event_date DESC, id DESC
    LIMIT $1
    `,
    [limit]
  );
  return rows as FederalEventRow[];
}

export async function getLatestFederalSnapshot() {
  const { rows } = await pool.query(
    `
    SELECT id, snapshot_date::text AS snapshot_date,
           overall_power_stability, executive_cohesion, cabinet_stability,
           legislative_alignment, conflict_temperature, narrative_pressure,
           note, created_at::text AS created_at
    FROM federal_timeline_snapshots
    ORDER BY snapshot_date DESC, id DESC
    LIMIT 1
    `
  );
  return rows[0] ?? null;
}

export async function getRecentFederalSnapshots(limit = 14) {
  const { rows } = await pool.query(
    `
    SELECT id, snapshot_date::text AS snapshot_date,
           overall_power_stability, executive_cohesion, cabinet_stability,
           legislative_alignment, conflict_temperature, narrative_pressure,
           note
    FROM federal_timeline_snapshots
    ORDER BY snapshot_date DESC, id DESC
    LIMIT $1
    `,
    [limit]
  );
  return rows;
}

export async function getEventActorsForEvent(eventId: number) {
  const { rows } = await pool.query(
    `
    SELECT fea.role, fea.stance, a.id AS actor_id, a.slug, a.name, a.actor_type
    FROM federal_event_actors fea
    JOIN federal_actors a ON a.id = fea.actor_id
    WHERE fea.event_id = $1
    ORDER BY fea.id ASC
    `,
    [eventId]
  );
  return rows;
}

export async function getEventsWithActorNames(limit = 40): Promise<
  (FederalEventRow & { actors_label: string })[]
> {
  const events = await getLatestFederalEvents(limit);
  const out = [];
  for (const e of events) {
    const actors = await getEventActorsForEvent(e.id);
    const actors_label = actors
      .map((r) => `${(r as { name: string }).name} (${(r as { role: string }).role})`)
      .join("; ");
    out.push({ ...e, actors_label });
  }
  return out;
}

export async function getAllActorsList(): Promise<FederalActorRow[]> {
  const { rows } = await pool.query(
    `
    SELECT id, slug, name, actor_type, office_title, branch, department, is_active,
           formal_power_score, political_influence_score, x_signal_score, conflict_index,
           agenda_alignment_score, power_status, summary,
           last_seen_at::text AS last_seen_at
    FROM federal_actors
    WHERE country_code = 'us'
    ORDER BY is_active DESC, formal_power_score DESC NULLS LAST, name ASC
    `
  );
  return rows as FederalActorRow[];
}

export async function getActorBySlug(slug: string): Promise<FederalActorRow | null> {
  const { rows } = await pool.query(
    `
    SELECT id, slug, name, actor_type, office_title, branch, department, is_active,
           formal_power_score, political_influence_score, x_signal_score, conflict_index,
           agenda_alignment_score, power_status, summary,
           last_seen_at::text AS last_seen_at
    FROM federal_actors
    WHERE slug = $1 AND country_code = 'us'
    `,
    [slug]
  );
  return (rows[0] as FederalActorRow) ?? null;
}

export async function getPositionHistoryForActor(actorId: number) {
  const { rows } = await pool.query(
    `
    SELECT aph.id, aph.start_date::text AS start_date, aph.end_date::text AS end_date,
           aph.status, aph.source_type, aph.source_name, aph.source_url, aph.confidence,
           p.office_title, p.branch, p.department, p.formal_power_weight
    FROM actor_position_history aph
    JOIN federal_positions p ON p.id = aph.position_id
    WHERE aph.actor_id = $1
    ORDER BY aph.start_date DESC, aph.id DESC
    `,
    [actorId]
  );
  return rows;
}

export async function getEventsForActor(actorId: number, limit = 20) {
  const { rows } = await pool.query(
    `
    SELECT e.id, e.event_type, e.title, e.summary, e.event_date::text AS event_date,
           e.branch, e.department, e.impact_direction, e.impact_strength,
           e.source_type, e.confidence, fea.role, fea.stance
    FROM federal_event_actors fea
    JOIN federal_events e ON e.id = fea.event_id
    WHERE fea.actor_id = $1
    ORDER BY e.event_date DESC, e.id DESC
    LIMIT $2
    `,
    [actorId, limit]
  );
  return rows;
}

/** Count event involvements for an actor since a calendar date (inclusive). */
export async function countActorEventsSince(
  actorId: number,
  sinceDate: string
): Promise<number> {
  const { rows } = await pool.query(
    `
    SELECT COUNT(*)::int AS c
    FROM federal_event_actors fea
    JOIN federal_events e ON e.id = fea.event_id
    WHERE fea.actor_id = $1 AND e.event_date >= $2::date
    `,
    [actorId, sinceDate]
  );
  return rows[0]?.c ?? 0;
}

export async function getFederalEventConsequences(eventId: number) {
  const { rows } = await pool.query(
    `
    SELECT fec.id, fec.target_type, fec.target_actor_id, fec.target_department,
           fec.dimension, fec.impact_value, fec.time_horizon, fec.confidence, fec.explanation,
           a.name AS target_actor_name
    FROM federal_event_consequences fec
    LEFT JOIN federal_actors a ON a.id = fec.target_actor_id
    WHERE fec.event_id = $1
    ORDER BY fec.id ASC
    `,
    [eventId]
  );
  return rows;
}

export async function getCausalOverviewData() {
  const events = await getLatestFederalEvents(25);
  const enriched = [];
  for (const e of events) {
    const actors = await getEventActorsForEvent(e.id);
    const cons = await getFederalEventConsequences(e.id);
    enriched.push({ event: e, actors, consequences: cons });
  }
  return enriched;
}

export type FederalEventChainStep = {
  event: Record<string, unknown>;
  actors: unknown[];
  consequences: unknown[];
};

/** Walk up parent_event_id to the root of the chain. */
export async function findFederalEventRootId(eventId: number): Promise<number> {
  let cur = eventId;
  for (let i = 0; i < 64; i++) {
    const { rows } = await pool.query(
      `SELECT id, parent_event_id FROM federal_events WHERE id = $1`,
      [cur]
    );
    const row = rows[0] as { id: number; parent_event_id: number | null } | undefined;
    if (!row) return eventId;
    if (row.parent_event_id == null) return row.id;
    cur = row.parent_event_id;
  }
  return eventId;
}

/**
 * Ordered chain from root → leaves (recursive children by parent_event_id).
 */
export async function getEventChainFromRoot(rootId: number): Promise<FederalEventChainStep[]> {
  const { rows: events } = await pool.query(
    `
    WITH RECURSIVE down AS (
      SELECT * FROM federal_events WHERE id = $1
      UNION ALL
      SELECT fe.* FROM federal_events fe
      JOIN down ON fe.parent_event_id = down.id
    )
    SELECT * FROM down ORDER BY event_date ASC, id ASC
    `,
    [rootId]
  );
  const out: FederalEventChainStep[] = [];
  for (const ev of events) {
    const actors = await getEventActorsForEvent((ev as { id: number }).id);
    const consequences = await getFederalEventConsequences((ev as { id: number }).id);
    out.push({ event: ev as Record<string, unknown>, actors, consequences });
  }
  return out;
}

/** Full causal chain containing the given event (walks to root, then subtree). */
export async function getEventChain(eventId: number): Promise<FederalEventChainStep[]> {
  const rootId = await findFederalEventRootId(eventId);
  return getEventChainFromRoot(rootId);
}

/** Roots that have at least one child (linked causal chains). */
export async function getCausalChainsOverview(limitRoots = 6): Promise<FederalEventChainStep[][]> {
  const { rows: roots } = await pool.query(
    `
    SELECT fe.id
    FROM federal_events fe
    WHERE fe.parent_event_id IS NULL
      AND EXISTS (SELECT 1 FROM federal_events c WHERE c.parent_event_id = fe.id)
    ORDER BY fe.event_date DESC
    LIMIT $1
    `,
    [limitRoots]
  );
  const chains: FederalEventChainStep[][] = [];
  for (const r of roots) {
    chains.push(await getEventChainFromRoot((r as { id: number }).id));
  }
  return chains;
}

export type TimelineMergedItem =
  | { kind: "snapshot"; date: string; label: string; body: string; meta: string }
  | { kind: "event"; date: string; label: string; body: string; meta: string }
  | { kind: "position"; date: string; label: string; body: string; meta: string };

export async function getFederalTimelineMerged(limit = 50): Promise<TimelineMergedItem[]> {
  const snaps = await pool.query(
    `
    SELECT snapshot_date::text AS d, note, overall_power_stability, conflict_temperature
    FROM federal_timeline_snapshots
    ORDER BY snapshot_date DESC, id DESC
    LIMIT 20
    `
  );
  const evs = await pool.query(
    `
    SELECT id, event_date::text AS d, title, summary, event_type, confidence, source_type
    FROM federal_events
    ORDER BY event_date DESC, id DESC
    LIMIT 30
    `
  );
  const pos = await pool.query(
    `
    SELECT aph.start_date::text AS d, a.name AS actor_name, p.office_title, aph.status, aph.confidence
    FROM actor_position_history aph
    JOIN federal_actors a ON a.id = aph.actor_id
    JOIN federal_positions p ON p.id = aph.position_id
    ORDER BY aph.start_date DESC, aph.id DESC
    LIMIT 25
    `
  );

  const items: TimelineMergedItem[] = [];
  for (const r of snaps.rows) {
    items.push({
      kind: "snapshot",
      date: r.d,
      label: "Power structure snapshot",
      body: r.note || `Stability ${r.overall_power_stability ?? "—"}, conflict temp ${r.conflict_temperature ?? "—"}`,
      meta: `federal_timeline_snapshots · confidence: model`,
    });
  }
  for (const r of evs.rows) {
    items.push({
      kind: "event",
      date: r.d,
      label: r.title,
      body: (r.summary as string) || "",
      meta: `${r.event_type} · ${r.source_type} · ${r.confidence}`,
    });
  }
  for (const r of pos.rows) {
    items.push({
      kind: "position",
      date: r.d,
      label: `${r.actor_name} → ${r.office_title}`,
      body: `Status: ${r.status}`,
      meta: `position history · ${r.confidence}`,
    });
  }

  items.sort((a, b) => {
    const ta = new Date(a.date).getTime();
    const tb = new Date(b.date).getTime();
    return tb - ta;
  });

  return items.slice(0, limit);
}
