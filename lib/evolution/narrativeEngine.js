/**
 * Rule-based evolution narrative from snapshots, trends, consequences, and event chains.
 * @param {import("pg").Pool} pool
 * @param {string} countryCode
 */
const { classifySeries, computeCountrySnapshotTrends } = require("./trendEngine.js");

function directionSignal(overallSeries, overallClassify) {
  const l = String(overallClassify.label || "").toLowerCase();
  if (l.includes("volatile")) return "volatile";
  if (l.includes("accelerating")) return "accelerating";
  if (l.includes("rising")) return "rising";
  if (l.includes("declining")) return "declining";
  if (overallSeries.length >= 2) {
    const a = overallSeries[0];
    const b = overallSeries[overallSeries.length - 1];
    if (b > a + 1) return "rising";
    if (b < a - 1) return "declining";
    if (Math.abs(b - a) <= 1) return "stable";
  }
  return "stable";
}

function dimDelta(first, last, key) {
  const a = Number(first?.[key]);
  const b = Number(last?.[key]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return 0;
  return Math.round((b - a) * 100) / 100;
}

async function buildCountryEvolutionNarrative(pool, countryCode) {
  const N = 5;
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
    [countryCode, N]
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

  const oCls = classifySeries(overallSeries, "overall");
  const direction = directionSignal(overallSeries, oCls);

  let trendBundle = null;
  if (chron.length >= 2) {
    try {
      trendBundle = computeCountrySnapshotTrends({
        overallSeries,
        powerSeries,
        riskSeries,
        opportunitySeries,
      });
    } catch {
      trendBundle = null;
    }
  }

  const dimKeys = [
    "governance",
    "economy",
    "innovation",
    "infrastructure",
    "openness",
    "future_potential",
  ];
  const deltas = [];
  if (first && last) {
    for (const k of dimKeys) {
      const d = dimDelta(first, last, k);
      if (Math.abs(d) > 0.05) deltas.push({ dim: k, delta: d });
    }
    deltas.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  }

  const key_drivers = [];
  for (const x of deltas.filter((d) => d.delta > 0).slice(0, 4)) {
    key_drivers.push(
      `${x.dim}: +${x.delta} points vs earlier snapshot window (ledger-backed dimensions).`
    );
  }
  if (key_drivers.length === 0 && last) {
    key_drivers.push(
      `Latest overall ${Number(last.overall).toFixed(0)}; power ${last.power_score != null ? Number(last.power_score).toFixed(1) : "—"} (model).`
    );
  }

  const risks = [];
  const riskCls = classifySeries(riskSeries, "risk");
  if (riskCls.label && !String(riskCls.label).includes("stable")) {
    risks.push(`Risk series: ${riskCls.label} (${riskCls.detail}).`);
  }
  if (trendBundle?.risk_trend) {
    risks.push(String(trendBundle.risk_trend).slice(0, 220));
  }
  for (const x of deltas.filter((d) => d.delta < 0).slice(0, 2)) {
    risks.push(`${x.dim} softened by ${Math.abs(x.delta)} vs window start.`);
  }
  if (risks.length === 0) {
    risks.push("No strong negative dimension drift in the current snapshot window.");
  }

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
      LIMIT 6
      `,
      [countryCode]
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
      LIMIT 6
      `,
      [countryCode]
    );
  }

  const turning_points = [];
  for (const r of evRes.rows) {
    const chainNote = r.parent_event_id
      ? "Linked in event chain (follows a prior national event)."
      : "Standalone ledger entry in this window.";
    turning_points.push({
      event: String(r.title),
      impact: r.dim_summary
        ? `Consequence dimensions: ${r.dim_summary} (magnitude ${Number(r.mag).toFixed(1)}).`
        : `Recorded impact weight ${Number(r.mag).toFixed(1)}.`,
      why_important: `[Ranked by summed |consequence| on ledger, not by date.] ${chainNote} Type: ${r.event_type}. ${r.summary ? String(r.summary).slice(0, 140) : ""}`,
    });
  }

  if (turning_points.length === 0) {
    let fallback;
    try {
      fallback = await pool.query(
        `
        SELECT id, title, event_type, event_date, summary, parent_event_id
        FROM events
        WHERE country_code = $1
        ORDER BY event_date DESC NULLS LAST, id DESC
        LIMIT 4
        `,
        [countryCode]
      );
    } catch {
      fallback = await pool.query(
        `
        SELECT id, title, event_type, event_date, summary
        FROM events
        WHERE country_code = $1
        ORDER BY event_date DESC NULLS LAST, id DESC
        LIMIT 4
        `,
        [countryCode]
      );
    }
    for (const r of fallback.rows) {
      const chained = Boolean(r.parent_event_id);
      turning_points.push({
        event: String(r.title),
        impact: "Event logged; detailed consequence rows may be pending backfill.",
        why_important: `${r.event_type} on ${String(r.event_date).slice(0, 10)}. ${chained ? "Part of a chain." : "Recent national-level signal."}`,
      });
    }
  }

  const nameRes = await pool.query(`SELECT name FROM countries WHERE code = $1`, [countryCode]);
  const cname = nameRes.rows[0]?.name || countryCode;

  const tOverall = trendBundle?.overall_trend_extended || `${oCls.label} — ${oCls.detail}`;
  const tPower = trendBundle?.power_trend_extended || classifySeries(powerSeries, "power").label;
  const tRisk = trendBundle?.risk_trend || riskCls.detail;

  const summaryParts = [];
  summaryParts.push(
    `${cname}: from the last ${chron.length || 0} score snapshot(s), overall moved ${overallSeries.length >= 2 ? `from ~${overallSeries[0].toFixed(0)} to ~${overallSeries[overallSeries.length - 1].toFixed(0)}` : "within the available ledger"}.`
  );
  summaryParts.push(`Direction signal (rule): ${direction}. Trend read: ${tOverall.slice(0, 180)}`);
  if (chron.length >= 2) {
    summaryParts.push(`Power trajectory hint: ${String(tPower).slice(0, 140)}`);
    summaryParts.push(`Risk/opportunity context: ${String(tRisk).slice(0, 140)}`);
  }

  return {
    summary: summaryParts.join(" "),
    direction,
    turning_points: turning_points.slice(0, 5),
    key_drivers: key_drivers.slice(0, 5),
    risks: risks.slice(0, 5),
  };
}

module.exports = { buildCountryEvolutionNarrative };
