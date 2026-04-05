/**
 * Rule-based evolution contrast between two countries (snapshots, trends, consequences, chains).
 * @param {import("pg").Pool} pool
 * @param {string} leftCode
 * @param {string} rightCode
 */
const { classifySeries, computeCountrySnapshotTrends } = require("./trendEngine.js");

async function loadSnapWindow(pool, countryCode, n) {
  const snapRes = await pool.query(
    `
    SELECT id, snapshot_date, overall, governance, economy, innovation, openness,
           infrastructure, future_potential, human_capital, social_order,
           risk, opportunity, power_score
    FROM country_score_snapshots
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT $2
    `,
    [countryCode, n]
  );
  const chron = [...snapRes.rows].reverse();
  const first = chron[0] || null;
  const last = chron[chron.length - 1] || null;
  const overallSeries = chron.map((r) => Number(r.overall ?? 0));
  const powerSeries = chron.map((r) =>
    r.power_score != null && Number.isFinite(Number(r.power_score))
      ? Number(r.power_score)
      : Number(r.overall ?? 0)
  );
  const riskSeries = chron.map((r) => Number(r.risk ?? 0));
  const opportunitySeries = chron.map((r) => Number(r.opportunity ?? 0));
  return { chron, first, last, overallSeries, powerSeries, riskSeries, opportunitySeries };
}

function dimDelta(first, last, key) {
  const a = Number(first?.[key]);
  const b = Number(last?.[key]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return 0;
  return Math.round((b - a) * 100) / 100;
}

function driversFromWindow(first, last, dimKeys) {
  const deltas = [];
  if (!first || !last) return [];
  for (const k of dimKeys) {
    const d = dimDelta(first, last, k);
    if (Math.abs(d) > 0.05) deltas.push({ dim: k, delta: d });
  }
  deltas.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  return deltas;
}

async function topEventsByConsequenceMag(pool, countryCode, limit, name, otherTrendLabel) {
  let evRes;
  try {
    evRes = await pool.query(
      `
      SELECT e.id, e.title, e.event_type, e.event_date, e.summary, e.parent_event_id,
             SUM(ABS(ec.impact_value))::float AS mag,
             STRING_AGG(
               TRIM(ec.dimension) || ' ' || ROUND(ec.impact_value::numeric, 2)::text,
               ', ' ORDER BY ec.dimension
             ) AS dim_summary
      FROM events e
      JOIN event_consequences ec
        ON ec.event_id = e.id AND ec.target_country_code = e.country_code
      WHERE e.country_code = $1
      GROUP BY e.id, e.title, e.event_type, e.event_date, e.summary, e.parent_event_id
      ORDER BY mag DESC NULLS LAST
      LIMIT $2
      `,
      [countryCode, limit]
    );
  } catch {
    evRes = await pool.query(
      `
      SELECT e.id, e.title, e.event_type, e.event_date, e.summary,
             SUM(ABS(ec.impact_value))::float AS mag,
             STRING_AGG(
               TRIM(ec.dimension) || ' ' || ROUND(ec.impact_value::numeric, 2)::text,
               ', ' ORDER BY ec.dimension
             ) AS dim_summary
      FROM events e
      JOIN event_consequences ec
        ON ec.event_id = e.id AND ec.target_country_code = e.country_code
      WHERE e.country_code = $1
      GROUP BY e.id, e.title, e.event_type, e.event_date, e.summary
      ORDER BY mag DESC NULLS LAST
      LIMIT $2
      `,
      [countryCode, limit]
    );
  }

  const out = [];
  for (const r of evRes.rows) {
    const chain = r.parent_event_id
      ? "Recorded in a linked national event chain."
      : "Standalone ledger entry.";
    out.push({
      event: `[${name}] ${r.title}`,
      impact: r.dim_summary
        ? `Dimensions: ${r.dim_summary} (weight ${Number(r.mag).toFixed(1)}).`
        : `Impact weight ${Number(r.mag).toFixed(1)}.`,
      why_it_mattered: `${chain} Other country’s overall trend label (for contrast only, not causal): ${otherTrendLabel}. Type ${r.event_type}.`,
    });
  }
  return out;
}

async function recentEventsFallback(pool, countryCode, name, otherTrendLabel, limit) {
  let fb;
  try {
    fb = await pool.query(
      `
      SELECT id, title, event_type, event_date, summary, parent_event_id
      FROM events
      WHERE country_code = $1
      ORDER BY event_date DESC NULLS LAST, id DESC
      LIMIT $2
      `,
      [countryCode, limit]
    );
  } catch {
    try {
      fb = await pool.query(
        `
        SELECT id, title, event_type, event_date, summary
        FROM events
        WHERE country_code = $1
        ORDER BY event_date DESC NULLS LAST, id DESC
        LIMIT $2
        `,
        [countryCode, limit]
      );
    } catch {
      return [];
    }
  }
  return fb.rows.map((r) => ({
    event: `[${name}] ${r.title}`,
    impact: "Consequence rows sparse; event logged for timeline.",
    why_it_mattered: `${r.event_type} on ${String(r.event_date).slice(0, 10)}. Other country overall label (contrast only): ${otherTrendLabel}.`,
  }));
}

async function chainDensity(pool, countryCode) {
  try {
    const { rows } = await pool.query(
      `
      SELECT COUNT(*)::int AS chained
      FROM events
      WHERE country_code = $1 AND parent_event_id IS NOT NULL
      `,
      [countryCode]
    );
    return Number(rows[0]?.chained ?? 0);
  } catch {
    return 0;
  }
}

async function buildCountryEvolutionContrast(pool, leftCode, rightCode) {
  const { rows: names } = await pool.query(
    `SELECT code, name FROM countries WHERE code = ANY($1::text[])`,
    [[leftCode, rightCode]]
  );
  const nameBy = Object.fromEntries(names.map((r) => [r.code, r.name]));
  const leftName = nameBy[leftCode] || leftCode;
  const rightName = nameBy[rightCode] || rightCode;

  const L = await loadSnapWindow(pool, leftCode, 5);
  const R = await loadSnapWindow(pool, rightCode, 5);

  const oL = classifySeries(L.overallSeries, "overall");
  const oR = classifySeries(R.overallSeries, "overall");
  const pL = classifySeries(L.powerSeries, "power");
  const pR = classifySeries(R.powerSeries, "power");
  const rL = classifySeries(L.riskSeries, "risk");
  const rR = classifySeries(R.riskSeries, "risk");

  const direction_gap = `${leftName} overall: ${oL.label} — ${oL.detail}. ${rightName} overall: ${oR.label} — ${oR.detail}. Power series: ${leftName} ${pL.label}; ${rightName} ${pR.label}.`;

  const dimKeys = [
    "governance",
    "economy",
    "innovation",
    "infrastructure",
    "openness",
    "future_potential",
  ];
  const dL = driversFromWindow(L.first, L.last, dimKeys);
  const dR = driversFromWindow(R.first, R.last, dimKeys);

  const left_drivers = [];
  for (const x of dL.filter((d) => d.delta > 0).slice(0, 4)) {
    left_drivers.push(
      `${leftName} — ${x.dim}: +${x.delta} over snapshot window (ledger).`
    );
  }
  if (left_drivers.length === 0 && L.last) {
    left_drivers.push(
      `${leftName}: latest overall ${Number(L.last.overall).toFixed(0)}; power ${L.last.power_score != null ? Number(L.last.power_score).toFixed(1) : "—"}.`
    );
  }

  const right_drivers = [];
  for (const x of dR.filter((d) => d.delta > 0).slice(0, 4)) {
    right_drivers.push(
      `${rightName} — ${x.dim}: +${x.delta} over snapshot window (ledger).`
    );
  }
  if (right_drivers.length === 0 && R.last) {
    right_drivers.push(
      `${rightName}: latest overall ${Number(R.last.overall).toFixed(0)}; power ${R.last.power_score != null ? Number(R.last.power_score).toFixed(1) : "—"}.`
    );
  }

  let trendL = "";
  let trendR = "";
  if (L.chron.length >= 2) {
    try {
      const t = computeCountrySnapshotTrends({
        overallSeries: L.overallSeries,
        powerSeries: L.powerSeries,
        riskSeries: L.riskSeries,
        opportunitySeries: L.opportunitySeries,
      });
      trendL = t.overall_trend_extended || "";
    } catch {
      trendL = "";
    }
  }
  if (R.chron.length >= 2) {
    try {
      const t = computeCountrySnapshotTrends({
        overallSeries: R.overallSeries,
        powerSeries: R.powerSeries,
        riskSeries: R.riskSeries,
        opportunitySeries: R.opportunitySeries,
      });
      trendR = t.overall_trend_extended || "";
    } catch {
      trendR = "";
    }
  }

  const soL = L.last ? Number(L.last.social_order ?? 0) : null;
  const soR = R.last ? Number(R.last.social_order ?? 0) : null;
  const chainL = await chainDensity(pool, leftCode);
  const chainR = await chainDensity(pool, rightCode);

  const stability_comparison = `${leftName} risk inertia: ${rL.label}. ${rightName}: ${rR.label}. Social order (latest snapshot): ${soL != null ? soL.toFixed(0) : "—"} vs ${soR != null ? soR.toFixed(0) : "—"}. Linked event edges (parent_event_id): ${leftName} ${chainL}, ${rightName} ${chainR}.`;

  const divL = await topEventsByConsequenceMag(pool, leftCode, 3, leftName, oR.label);
  const divR = await topEventsByConsequenceMag(pool, rightCode, 3, rightName, oL.label);
  let divergence_points = [...divL, ...divR].slice(0, 6);
  if (divergence_points.length === 0) {
    const fL = await recentEventsFallback(pool, leftCode, leftName, oR.label, 2);
    const fR = await recentEventsFallback(pool, rightCode, rightName, oL.label, 2);
    divergence_points = [...fL, ...fR].slice(0, 6);
  }

  const powerGap =
    L.last && R.last && L.last.power_score != null && R.last.power_score != null
      ? Math.round((Number(L.last.power_score) - Number(R.last.power_score)) * 100) / 100
      : null;

  const summary = [
    `Paired ledger read (associational; not causal across countries): ${leftName} vs ${rightName} show different overall labels (${oL.label} vs ${oR.label}).`,
    powerGap != null
      ? `Latest model power gap (left − right): ${powerGap > 0 ? "+" : ""}${powerGap}.`
      : "Power snapshot comparison uses latest country_score_snapshots.",
    trendL || trendR
      ? `Trend bundle (where history allows): ${leftName.slice(0, 24)} ${trendL.slice(0, 120)} · ${rightName.slice(0, 24)} ${trendR.slice(0, 120)}`
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const bottom_line = `Ledger contrast: ${leftName} overall inertia reads "${oL.label}" vs ${rightName} "${oR.label}" — different snapshot windows and recorded event/consequence mixes are associated with this divergence (not a proof of causal linkage).`;

  return {
    summary,
    direction_gap,
    divergence_points,
    left_drivers,
    right_drivers,
    stability_comparison,
    bottom_line,
  };
}

module.exports = { buildCountryEvolutionContrast };
