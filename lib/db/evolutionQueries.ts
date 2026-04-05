import { pool } from "@/lib/db";

export type TimelineEntry =
  | {
      kind: "event";
      sort_ts: string;
      id: number;
      title: string;
      body: string;
      meta: string;
      event_type: string;
      impact_direction: string;
      impact_strength: number;
    }
  | {
      kind: "snapshot";
      sort_ts: string;
      id: number;
      title: string;
      body: string;
      meta: string;
      overall: number;
      power_score: number | null;
      power_delta: number | null;
      power_trend: string | null;
      power_note: string | null;
    };

export async function getTimelineForCountry(
  code: string,
  limit = 50
): Promise<TimelineEntry[]> {
  const [ev, sn] = await Promise.all([
    pool.query(
      `
      SELECT
        id,
        title,
        summary,
        event_type,
        event_date,
        impact_direction,
        impact_strength,
        source_name
      FROM events
      WHERE country_code = $1
      ORDER BY event_date DESC, id DESC
      LIMIT $2
      `,
      [code, limit]
    ),
    pool.query(
      `
      SELECT
        id,
        snapshot_date,
        note,
        overall,
        power_score,
        power_delta,
        power_trend,
        power_note,
        governance,
        economy,
        innovation,
        risk
      FROM country_score_snapshots
      WHERE country_code = $1
      ORDER BY id DESC
      LIMIT $2
      `,
      [code, limit]
    ),
  ]);

  const entries: TimelineEntry[] = [];

  for (const r of ev.rows) {
    const d = r.event_date;
    const sort_ts =
      d instanceof Date ? d.toISOString() : String(d).slice(0, 10) + "T12:00:00Z";
    entries.push({
      kind: "event",
      sort_ts,
      id: r.id,
      title: r.title,
      body: r.summary || "",
      meta: `${r.event_type} · ${r.impact_direction} ${r.impact_strength}`,
      event_type: r.event_type,
      impact_direction: r.impact_direction,
      impact_strength: Number(r.impact_strength),
    });
  }

  for (const r of sn.rows) {
    const d = r.snapshot_date;
    const sort_ts =
      d instanceof Date ? d.toISOString() : String(d).slice(0, 10) + "T12:00:00Z";
    entries.push({
      kind: "snapshot",
      sort_ts,
      id: r.id,
      title: "State snapshot",
      body:
        r.power_note ||
        r.note ||
        `Overall ${r.overall}, power ${r.power_score ?? "—"}`,
      meta: `overall ${r.overall} · power ${r.power_score ?? "—"}`,
      overall: Number(r.overall),
      power_score:
        r.power_score != null ? Number(r.power_score) : null,
      power_delta:
        r.power_delta != null ? Number(r.power_delta) : null,
      power_trend: r.power_trend != null ? String(r.power_trend) : null,
      power_note: r.power_note != null ? String(r.power_note) : null,
    });
  }

  entries.sort((a, b) => {
    const ta = new Date(a.sort_ts).getTime();
    const tb = new Date(b.sort_ts).getTime();
    if (tb !== ta) return tb - ta;
    return b.id - a.id;
  });

  return entries.slice(0, limit);
}

export type ConsequenceRow = {
  dimension: string;
  impact_value: number;
  time_horizon: string;
  confidence: number;
  explanation: string | null;
};

export type EventWithCausal = {
  id: number;
  title: string;
  summary: string;
  event_type: string;
  event_date: string;
  impact_direction: string;
  impact_strength: number;
  consequences: ConsequenceRow[];
  actors: { name: string; actor_type: string; role: string }[];
};

export async function getEventsWithCausalForCountry(
  code: string,
  limit = 12
): Promise<EventWithCausal[]> {
  const { rows: events } = await pool.query(
    `
    SELECT id, title, summary, event_type, event_date, impact_direction, impact_strength
    FROM events
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT $2
    `,
    [code, limit]
  );

  const out: EventWithCausal[] = [];

  for (const e of events) {
    let cons;
    let acts;
    try {
      cons = await pool.query(
        `
        SELECT dimension, impact_value, time_horizon, confidence, explanation
        FROM event_consequences
        WHERE event_id = $1
        ORDER BY id ASC
        `,
        [e.id]
      );
    } catch {
      cons = { rows: [] };
    }
    try {
      acts = await pool.query(
        `
        SELECT a.name, a.actor_type, ea.role
        FROM event_actors ea
        JOIN actors a ON a.id = ea.actor_id
        WHERE ea.event_id = $1
        `,
        [e.id]
      );
    } catch {
      acts = { rows: [] };
    }

    out.push({
      id: e.id,
      title: e.title,
      summary: e.summary,
      event_type: e.event_type,
      event_date: String(e.event_date),
      impact_direction: e.impact_direction,
      impact_strength: Number(e.impact_strength),
      consequences: cons.rows.map((c) => ({
        dimension: c.dimension,
        impact_value: Number(c.impact_value),
        time_horizon: c.time_horizon,
        confidence: Number(c.confidence),
        explanation: c.explanation,
      })),
      actors: acts.rows.map((a) => ({
        name: a.name,
        actor_type: a.actor_type,
        role: a.role,
      })),
    });
  }

  return out;
}

export async function getCachedInsight(
  cacheKey: string
): Promise<Record<string, unknown> | null> {
  try {
    const { rows } = await pool.query(
      `SELECT payload FROM ai_insights_cache WHERE cache_key = $1`,
      [cacheKey]
    );
    if (!rows[0]?.payload) return null;
    return rows[0].payload as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function upsertInsightCache(
  cacheKey: string,
  payload: Record<string, unknown>
): Promise<void> {
  await pool.query(
    `
    INSERT INTO ai_insights_cache (cache_key, payload, updated_at)
    VALUES ($1, $2::jsonb, NOW())
    ON CONFLICT (cache_key) DO UPDATE SET
      payload = EXCLUDED.payload,
      updated_at = NOW()
    `,
    [cacheKey, JSON.stringify(payload)]
  );
}
