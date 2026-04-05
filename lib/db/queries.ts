import { pool } from "@/lib/db";

export type CountryScoreBundle = {
  governance: number;
  social_order: number;
  economy: number;
  human_capital: number;
  infrastructure: number;
  innovation: number;
  openness: number;
  future_potential: number;
  overall: number;
  risk: number;
  opportunity: number;
  power_score: number | null;
  power_classification: string | null;
};

export type CountryCard = {
  code: string;
  name: string;
  region: string;
  capital: string;
  population: number;
  gdp: number;
  summary: string;
  score: CountryScoreBundle;
};

function mapScore(row: Record<string, unknown>): CountryScoreBundle {
  return {
    governance: Number(row.governance ?? 0),
    social_order: Number(row.social_order ?? 0),
    economy: Number(row.economy ?? 0),
    human_capital: Number(row.human_capital ?? 0),
    infrastructure: Number(row.infrastructure ?? 0),
    innovation: Number(row.innovation ?? 0),
    openness: Number(row.openness ?? 0),
    future_potential: Number(row.future_potential ?? 0),
    overall: Number(row.overall ?? 0),
    risk: Number(row.risk ?? 0),
    opportunity: Number(row.opportunity ?? 0),
    power_score:
      row.power_score != null && row.power_score !== ""
        ? Number(row.power_score)
        : null,
    power_classification:
      row.power_classification != null
        ? String(row.power_classification)
        : null,
  };
}

function mapCountryRow(row: Record<string, unknown>): CountryCard {
  return {
    code: String(row.code),
    name: String(row.name),
    region: String(row.region),
    capital: String(row.capital),
    population: Number(row.population),
    gdp: Number(row.gdp),
    summary: String(row.summary ?? ""),
    score: mapScore(row),
  };
}

export async function getCountryCardsFromDB(): Promise<CountryCard[]> {
  const result = await pool.query(`
    SELECT
      c.code,
      c.name,
      c.region,
      c.capital,
      c.population,
      c.gdp,
      c.summary,
      s.governance,
      s.social_order,
      s.economy,
      s.human_capital,
      s.infrastructure,
      s.innovation,
      s.openness,
      s.future_potential,
      s.overall,
      s.risk,
      s.opportunity,
      s.power_score,
      s.power_classification
    FROM countries c
    JOIN country_scores s
      ON c.code = s.country_code
    ORDER BY c.name ASC
  `);

  return result.rows.map((row) => mapCountryRow(row));
}

export async function getRankingsFromDB(): Promise<CountryCard[]> {
  const result = await pool.query(`
    SELECT
      c.code,
      c.name,
      c.region,
      c.capital,
      c.population,
      c.gdp,
      c.summary,
      s.governance,
      s.social_order,
      s.economy,
      s.human_capital,
      s.infrastructure,
      s.innovation,
      s.openness,
      s.future_potential,
      s.overall,
      s.risk,
      s.opportunity,
      s.power_score,
      s.power_classification
    FROM countries c
    JOIN country_scores s
      ON c.code = s.country_code
    ORDER BY s.overall DESC, c.name ASC
  `);

  return result.rows.map((row) => mapCountryRow(row));
}

export type PowerRow = CountryCard & {
  power_delta: number | null;
  power_trend: string | null;
  power_note: string | null;
};

export async function getPowerRowsFromDB(): Promise<PowerRow[]> {
  const result = await pool.query(`
    WITH latest_snap AS (
      SELECT DISTINCT ON (country_code)
        country_code,
        power_delta,
        power_trend,
        power_note
      FROM country_score_snapshots
      ORDER BY country_code, id DESC
    )
    SELECT
      c.code,
      c.name,
      c.region,
      c.capital,
      c.population,
      c.gdp,
      c.summary,
      s.governance,
      s.social_order,
      s.economy,
      s.human_capital,
      s.infrastructure,
      s.innovation,
      s.openness,
      s.future_potential,
      s.overall,
      s.risk,
      s.opportunity,
      s.power_score,
      s.power_classification,
      ls.power_delta,
      ls.power_trend,
      ls.power_note
    FROM countries c
    JOIN country_scores s ON c.code = s.country_code
    LEFT JOIN latest_snap ls ON ls.country_code = c.code
    ORDER BY s.power_score DESC NULLS LAST, c.name ASC
  `);

  return result.rows.map((row) => {
    const base = mapCountryRow(row);
    return {
      ...base,
      power_delta:
        row.power_delta != null && row.power_delta !== ""
          ? Number(row.power_delta)
          : null,
      power_trend:
        row.power_trend != null ? String(row.power_trend) : null,
      power_note: row.power_note != null ? String(row.power_note) : null,
    };
  });
}

export type CountryEventChainStep = {
  event: Record<string, unknown>;
  impacts: Record<string, unknown>[];
};

export async function findCountryEventRootId(eventId: number): Promise<number> {
  let cur = eventId;
  for (let i = 0; i < 64; i++) {
    const { rows } = await pool.query(
      `SELECT id, parent_event_id FROM events WHERE id = $1`,
      [cur]
    );
    const row = rows[0] as { id: number; parent_event_id: number | null } | undefined;
    if (!row) return eventId;
    if (row.parent_event_id == null) return row.id;
    cur = row.parent_event_id;
  }
  return eventId;
}

/** Full chain root → leaves with event_consequences as impacts. */
export async function getCountryEventChain(eventId: number): Promise<CountryEventChainStep[]> {
  const rootId = await findCountryEventRootId(eventId);
  const { rows: evs } = await pool.query(
    `
    WITH RECURSIVE down AS (
      SELECT * FROM events WHERE id = $1
      UNION ALL
      SELECT e.* FROM events e
      JOIN down ON e.parent_event_id = down.id
    )
    SELECT * FROM down ORDER BY event_date ASC, id ASC
    `,
    [rootId]
  );
  const out: CountryEventChainStep[] = [];
  for (const ev of evs) {
    const e = ev as { id: number; country_code: string };
    const { rows: impacts } = await pool.query(
      `
      SELECT dimension, impact_value, time_horizon, confidence, explanation
      FROM event_consequences
      WHERE event_id = $1 AND target_country_code = $2
      ORDER BY id ASC
      `,
      [e.id, e.country_code]
    );
    out.push({ event: ev as Record<string, unknown>, impacts });
  }
  return out;
}

export async function getComparePairFromDB(
  leftCode: string,
  rightCode: string
): Promise<{ left: CountryCard | null; right: CountryCard | null }> {
  const result = await pool.query(
    `
    SELECT
      c.code,
      c.name,
      c.region,
      c.capital,
      c.population,
      c.gdp,
      c.summary,
      s.governance,
      s.social_order,
      s.economy,
      s.human_capital,
      s.infrastructure,
      s.innovation,
      s.openness,
      s.future_potential,
      s.overall,
      s.risk,
      s.opportunity,
      s.power_score,
      s.power_classification
    FROM countries c
    JOIN country_scores s ON c.code = s.country_code
    WHERE c.code = ANY($1::text[])
    `,
    [[leftCode, rightCode]]
  );

  const byCode = new Map(result.rows.map((r) => [String(r.code), mapCountryRow(r)]));
  return {
    left: byCode.get(leftCode) ?? null,
    right: byCode.get(rightCode) ?? null,
  };
}
