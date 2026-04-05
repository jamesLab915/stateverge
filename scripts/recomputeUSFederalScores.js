const { createPgPool } = require("../lib/db/pgPool.js");
const {
  buildFederalConsequenceRowsFromTemplate,
} = require("../lib/evolution/consequenceTemplates.js");
const { computeFederalSnapshotTrends } = require("../lib/evolution/trendEngine.js");

const pool = createPgPool();

const CONFLICT_ROLES = new Set([
  "opposed",
  "criticized",
  "escalated",
  "removed",
  "harmed",
]);

function clamp(v, lo = 0, hi = 100) {
  if (!Number.isFinite(v)) return lo;
  return Math.round(Math.min(hi, Math.max(lo, v)) * 100) / 100;
}

function powerStatus(formal, influence, conflict) {
  const f = formal;
  const inf = influence;
  const c = conflict;
  if (f < 30) return "Marginal Actor";
  if (c >= 45) return "Contested Actor";
  if (f >= 95 && c < 18) return "Central Actor";
  if (f >= 90 && c >= 18) return "Contested Actor";
  if (inf > f + 8 && c < 35) return "Rising Actor";
  if (c >= 28 && f < 85 && f >= 45) return "Fragile Actor";
  if (f >= 85 && c < 25) return "Stable Actor";
  if (f >= 70) return "Stable Actor";
  return "Marginal Actor";
}

function normType(t) {
  return String(t || "")
    .trim()
    .toLowerCase();
}

async function ensureFederalConsequencesFromTemplates(client) {
  const { rows: events } = await client.query(`
    SELECT id, event_type, department, impact_strength, impact_direction, confidence
    FROM federal_events e
    WHERE NOT EXISTS (SELECT 1 FROM federal_event_consequences fec WHERE fec.event_id = e.id LIMIT 1)
    ORDER BY e.id ASC
  `);

  let inserted = 0;
  for (const e of events) {
    const rows = buildFederalConsequenceRowsFromTemplate(e);
    for (const r of rows) {
      await client.query(
        `
        INSERT INTO federal_event_consequences (
          event_id, target_type, target_actor_id, target_department,
          dimension, impact_value, time_horizon, confidence, explanation
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        `,
        [
          e.id,
          r.target_type,
          r.target_actor_id,
          r.target_department,
          r.dimension,
          r.impact_value,
          r.time_horizon,
          r.confidence,
          r.explanation,
        ]
      );
      inserted++;
    }
  }
  if (events.length) {
    console.log(`Template consequences: ${events.length} event(s) backfilled, ${inserted} row(s).`);
  }
}

function sameDeptRough(a, b) {
  const da = (a || "").toLowerCase();
  const db = (b || "").toLowerCase();
  if (!da || !db) return false;
  if (da.includes(db) || db.includes(da)) return true;
  const pa = da.split(/[\s,/]+/).filter(Boolean)[0];
  const pb = db.split(/[\s,/]+/).filter(Boolean)[0];
  return pa === pb;
}

function linkRule(parent, child) {
  const ta = normType(parent.event_type);
  const tb = normType(child.event_type);
  const days =
    (new Date(child.event_date).getTime() - new Date(parent.event_date).getTime()) /
    (86400 * 1000);
  if (days < 0 || days > 90) return false;

  if (ta === "dismissal" && tb === "appointment" && sameDeptRough(parent.department, child.department))
    return true;
  if (ta === "scandal" && tb === "investigation") return true;
  if (ta === "investigation" && tb === "resignation") return true;
  if (ta === "conflict" && tb === "policy_shift") return true;
  return false;
}

async function linkFederalEventChains(client) {
  const { rows } = await client.query(`
    SELECT id, event_type, event_date, department, parent_event_id
    FROM federal_events
    ORDER BY event_date ASC, id ASC
  `);

  const list = rows;
  let links = 0;
  for (let i = 0; i < list.length; i++) {
    const child = list[i];
    if (child.parent_event_id) continue;
    for (let j = i - 1; j >= 0; j--) {
      const parent = list[j];
      if (parent.event_date > child.event_date) continue;
      if (!linkRule(parent, child)) continue;
      const chk = await client.query(
        `UPDATE federal_events SET parent_event_id = $1 WHERE id = $2 AND parent_event_id IS NULL`,
        [parent.id, child.id]
      );
      if (chk.rowCount) links++;
      break;
    }
  }
  if (links) console.log(`Event chains linked: ${links} edge(s).`);
}

async function recomputeActorScores(client) {
  const { rows: actors } = await client.query(
    `SELECT id FROM federal_actors WHERE country_code = 'us'`
  );

  for (const { id: actorId } of actors) {
    const pos = await client.query(
      `
        SELECT p.formal_power_weight
        FROM actor_position_history aph
        JOIN federal_positions p ON p.id = aph.position_id
        WHERE aph.actor_id = $1
          AND aph.end_date IS NULL
          AND aph.status IN ('active', 'acting', 'confirmed')
        ORDER BY p.formal_power_weight DESC
        LIMIT 1
        `,
      [actorId]
    );

    let formal = 40;
    if (pos.rows[0]) {
      formal = clamp(Number(pos.rows[0].formal_power_weight), 0, 100);
    }

    const ev = await client.query(
      `
        SELECT fea.role, e.impact_strength, e.event_date
        FROM federal_event_actors fea
        JOIN federal_events e ON e.id = fea.event_id
        WHERE fea.actor_id = $1
          AND e.event_date >= CURRENT_DATE - INTERVAL '120 days'
        `,
      [actorId]
    );

    let conflictPts = 0;
    let eventCentrality = 0;
    for (const r of ev.rows) {
      const s = Number(r.impact_strength) || 3;
      if (CONFLICT_ROLES.has(String(r.role))) {
        conflictPts += s * 1.2;
      }
      if (["initiated", "signed", "nominated", "implemented"].includes(String(r.role))) {
        eventCentrality += s * 0.8;
      }
    }

    const cons = await client.query(
      `
        SELECT fec.impact_value, fec.dimension
        FROM federal_event_consequences fec
        JOIN federal_events e ON e.id = fec.event_id
        WHERE fec.target_actor_id = $1
          AND e.event_date >= CURRENT_DATE - INTERVAL '120 days'
        `,
      [actorId]
    );
    for (const c of cons.rows) {
      const dim = String(c.dimension);
      const v = Math.abs(Number(c.impact_value) || 0);
      if (dim === "conflict") conflictPts += v * 1.5;
      if (dim === "alignment") eventCentrality += v * 0.3;
    }

    const conflict_index = clamp(conflictPts, 0, 100);
    const influence = clamp(
      formal * 0.52 +
        Math.min(22, ev.rows.length * 2.2) +
        Math.min(18, eventCentrality) -
        Math.min(14, conflict_index * 0.14),
      0,
      100
    );

    const align = await client.query(
      `
        SELECT AVG(fec.impact_value) AS x
        FROM federal_event_consequences fec
        JOIN federal_events e ON e.id = fec.event_id
        WHERE fec.target_actor_id = $1 AND fec.dimension = 'alignment'
          AND e.event_date >= CURRENT_DATE - INTERVAL '180 days'
        `,
      [actorId]
    );
    let agenda_alignment_score = 50;
    if (align.rows[0]?.x != null) {
      agenda_alignment_score = clamp(50 + Number(align.rows[0].x) * 5, 0, 100);
    }

    const status = powerStatus(formal, influence, conflict_index);

    await client.query(
      `
        UPDATE federal_actors SET
          formal_power_score = $1,
          political_influence_score = $2,
          conflict_index = $3,
          agenda_alignment_score = $4,
          power_status = $5,
          updated_at = NOW()
        WHERE id = $6
        `,
      [formal, influence, conflict_index, agenda_alignment_score, status, actorId]
    );
  }

  console.log(`Recomputed scores for ${actors.length} federal actors.`);
}

async function writeSnapshotWithTrends(client) {
  const agg = await client.query(`
    SELECT
      AVG(formal_power_score)::float AS af,
      AVG(political_influence_score)::float AS ai,
      AVG(conflict_index)::float AS ac
    FROM federal_actors
    WHERE country_code = 'us' AND is_active = TRUE
  `);

  const af = Number(agg.rows[0]?.af) || 50;
  const ai = Number(agg.rows[0]?.ai) || 50;
  const ac = Number(agg.rows[0]?.ac) || 30;

  const hist = await client.query(
    `
    SELECT snapshot_date, overall_power_stability, conflict_temperature,
           aggregate_formal_power, aggregate_influence, aggregate_conflict
    FROM federal_timeline_snapshots
    ORDER BY snapshot_date DESC, id DESC
    LIMIT 5
    `
  );

  const chron = [...hist.rows].reverse();
  const powerSeries = chron.map((r) =>
    Number(r.aggregate_formal_power ?? r.overall_power_stability ?? 50)
  );
  const conflictSeries = chron.map((r) =>
    Number(r.aggregate_conflict ?? r.conflict_temperature ?? 50)
  );
  const influenceSeries = chron.map((r) => Number(r.aggregate_influence ?? 50));

  powerSeries.push(af);
  conflictSeries.push(ac);
  influenceSeries.push(ai);

  const trends = computeFederalSnapshotTrends({
    powerSeries,
    conflictSeries,
    influenceSeries,
  });

  const prev = await client.query(
    `
    SELECT executive_cohesion, cabinet_stability, legislative_alignment, narrative_pressure,
           overall_power_stability
    FROM federal_timeline_snapshots
    ORDER BY snapshot_date DESC, id DESC
    LIMIT 1
    `
  );
  const p = prev.rows[0] || {};

  const overall = clamp(
    (Number(p.overall_power_stability) || 50) * 0.7 + af * 0.3,
    0,
    100
  );

  await client.query(`DELETE FROM federal_timeline_snapshots WHERE snapshot_date = CURRENT_DATE`);

  await client.query(
    `
    INSERT INTO federal_timeline_snapshots (
      snapshot_date,
      overall_power_stability,
      executive_cohesion,
      cabinet_stability,
      legislative_alignment,
      conflict_temperature,
      narrative_pressure,
      aggregate_formal_power,
      aggregate_influence,
      aggregate_conflict,
      power_trend_extended,
      conflict_trend,
      influence_trend,
      note
    ) VALUES (
      CURRENT_DATE,
      $1, $2, $3, $4, $5, $6,
      $7, $8, $9,
      $10, $11, $12,
      $13
    )
    `,
    [
      overall,
      p.executive_cohesion ?? 55,
      p.cabinet_stability ?? 55,
      p.legislative_alignment ?? 50,
      clamp(ac, 0, 100),
      p.narrative_pressure ?? 50,
      af,
      ai,
      ac,
      trends.power_trend_extended,
      trends.conflict_trend,
      trends.influence_trend,
      "Evolution v2: aggregates from active actors; trends from last snapshots + current; consequences template-backed.",
    ]
  );

  console.log("Snapshot row inserted with trend inertia fields.");
}

async function markFederalEventsApplied(client) {
  await client.query(`
    UPDATE federal_events
    SET applied_to_scores = TRUE, applied_at = NOW()
    WHERE COALESCE(applied_to_scores, FALSE) = FALSE
  `);
}

async function main() {
  const client = await pool.connect();
  try {
    await ensureFederalConsequencesFromTemplates(client);
    await linkFederalEventChains(client);
    await recomputeActorScores(client);
    await writeSnapshotWithTrends(client);
    await markFederalEventsApplied(client);
  } finally {
    client.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
