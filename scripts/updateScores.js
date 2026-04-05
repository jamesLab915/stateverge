const { createPgPool } = require("../lib/db/pgPool.js");
const {
  computePowerScore,
  classifyPower,
  powerTrendFromScores,
  buildPowerNote,
} = require("../lib/powerMath.js");
const {
  buildCountryConsequenceRowsFromTemplate,
  buildDefaultCountryConsequenceRows,
} = require("../lib/evolution/consequenceTemplates.js");
const { buildConsequenceRows } = require("../lib/eventConsequenceMapping.js");
const { computeCountrySnapshotTrends } = require("../lib/evolution/trendEngine.js");
const { linkCountryEventChains } = require("../lib/evolution/countryEventChain.js");
const {
  clusterCountryEvents,
  applyContextScaleToDelta,
  clusterMergeFactor,
  emptyDeltas,
  applyNoiseCapsToDimensionDeltas,
  applyPowerScoreNoiseCap,
} = require("../lib/evolution/eventSignalQuality.js");
const {
  isEligibleForAutomaticScoreApplication,
} = require("../lib/evolution/eventSourcePolicy.js");

const pool = createPgPool();

function round2(x) {
  return Math.round(Number(x) * 100) / 100;
}

function clamp(v) {
  return Math.max(0, Math.min(100, Math.round(Number(v))));
}

function clampPower(v) {
  const x = Number(v);
  if (!Number.isFinite(x)) return 0;
  return Math.round(Math.max(0, Math.min(100, x)) * 100) / 100;
}

function overallFromDimensions(o) {
  const parts = [
    o.governance,
    o.social_order,
    o.economy,
    o.human_capital,
    o.infrastructure,
    o.innovation,
    o.openness,
    o.future_potential,
  ];
  const sum = parts.reduce((a, b) => a + Number(b || 0), 0);
  return Math.round(sum / parts.length);
}

async function ensureEventConsequencesForCountry(pool, e, code) {
  let cons = await pool.query(
    `
    SELECT dimension, impact_value
    FROM event_consequences
    WHERE event_id = $1 AND target_country_code = $2
    `,
    [e.id, code]
  );

  if (cons.rows.length > 0) return cons;

  let toInsert = buildCountryConsequenceRowsFromTemplate(e);
  if (toInsert.length === 0) {
    toInsert = buildConsequenceRows(e);
  }
  if (toInsert.length === 0) {
    toInsert = buildDefaultCountryConsequenceRows(e);
  }

  for (const r of toInsert) {
    await pool.query(
      `
      INSERT INTO event_consequences (
        event_id, target_country_code, dimension, impact_value,
        time_horizon, confidence, explanation
      ) VALUES ($1, $2, $3, $4, $5, $6, $7)
      `,
      [
        e.id,
        code,
        r.dimension,
        r.impact_value,
        r.time_horizon,
        r.confidence ?? 72,
        r.explanation,
      ]
    );
  }

  return pool.query(
    `
    SELECT dimension, impact_value
    FROM event_consequences
    WHERE event_id = $1 AND target_country_code = $2
    `,
    [e.id, code]
  );
}

/**
 * Apply pending events to country_scores and snapshots; mark events applied.
 * @returns {Promise<{
 *   step: string,
 *   pending: number,
 *   deferred: number,
 *   active: number,
 *   skippedNoCountryScores: number,
 *   countriesUpdated: number,
 *   eventsMarkedApplied: number,
 * }>}
 */
async function runUpdateScores() {
  const stats = {
    step: "scoring",
    pending: 0,
    deferred: 0,
    active: 0,
    skippedNoCountryScores: 0,
    countriesUpdated: 0,
    eventsMarkedApplied: 0,
  };
  try {
    console.log("Loading unapplied events...");

    const events = await pool.query(`
      SELECT * FROM events
      WHERE COALESCE(applied_to_scores, FALSE) = FALSE
      ORDER BY id ASC
    `);

    stats.pending = events.rows.length;

    if (events.rows.length === 0) {
      console.log("No pending events. Done.");
      return stats;
    }

    await linkCountryEventChains(pool);

    const allowLowTrust =
      process.env.EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES === "1";
    const deferred = events.rows.filter(
      (e) => !allowLowTrust && !isEligibleForAutomaticScoreApplication(e)
    );
    const active = events.rows.filter(
      (e) => allowLowTrust || isEligibleForAutomaticScoreApplication(e)
    );

    stats.deferred = deferred.length;
    stats.active = active.length;

    if (deferred.length > 0) {
      console.log(
        `Source policy: deferred ${deferred.length} event(s) (ai_generated / inferred / x_signal). Set EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1 to include them in scoring.`
      );
    }

    if (active.length === 0) {
      console.log(
        "No eligible events for score application (all deferred by source policy). Done."
      );
      return stats;
    }

    const byCountry = {};
    for (const e of active) {
      if (!byCountry[e.country_code]) byCountry[e.country_code] = [];
      byCountry[e.country_code].push(e);
    }

    const successfulCodes = new Set();

    for (const code of Object.keys(byCountry)) {
      const cs = await pool.query(`SELECT * FROM country_scores WHERE country_code = $1`, [code]);

      if (cs.rows.length === 0) {
        stats.skippedNoCountryScores++;
        console.log(`Skip ${code}: no country_scores row`);
        continue;
      }

      const g = emptyDeltas();
      let powerScoreAdjust = 0;

      const base = cs.rows[0];
      const clusters = clusterCountryEvents(byCountry[code], { windowDays: 3 });
      const merged = clusters.filter((c) => c.length > 1);
      if (merged.length > 0) {
        console.log(
          `  ${code}: merged ${merged.length} cluster(s) (same type, ≤3d gaps):`,
          merged.map((c) => `${c.length}×${c[0].event_type}`).join(", ")
        );
      }

      for (const cluster of clusters) {
        const mergeF = clusterMergeFactor(cluster.length);
        let clusterPower = 0;
        const clusterDim = emptyDeltas();

        for (const e of cluster) {
          const cons = await ensureEventConsequencesForCountry(pool, e, code);
          for (const c of cons.rows) {
            const k = String(c.dimension);
            const raw = Number(c.impact_value);
            const v = applyContextScaleToDelta(k, raw, base);
            if (k === "power_score") {
              clusterPower += v;
            } else if (clusterDim[k] !== undefined) {
              clusterDim[k] += v;
            }
          }
        }

        for (const k of Object.keys(clusterDim)) {
          g[k] += round2(clusterDim[k] * mergeF);
        }
        powerScoreAdjust += round2(clusterPower * mergeF);
      }

      applyNoiseCapsToDimensionDeltas(g, { maxPerDim: 4.5, maxL1: 28 });
      powerScoreAdjust = applyPowerScoreNoiseCap(powerScoreAdjust, {
        powerScoreCap: 3.5,
      });

      const next = {
        governance: clamp(base.governance + g.governance),
        social_order: clamp(base.social_order + g.social_order),
        economy: clamp(base.economy + g.economy),
        human_capital: clamp(base.human_capital + g.human_capital),
        infrastructure: clamp(base.infrastructure + g.infrastructure),
        innovation: clamp(base.innovation + g.innovation),
        openness: clamp(base.openness + g.openness),
        future_potential: clamp(base.future_potential + g.future_potential),
        risk: clamp(base.risk + g.risk),
        opportunity: clamp(base.opportunity + g.opportunity),
      };

      next.overall = overallFromDimensions(next);

      const basePower = computePowerScore(next);
      const power_score = clampPower(basePower + powerScoreAdjust);
      const power_classification = classifyPower({ ...next, power_score });

      const prevSnap = await pool.query(
        `
        SELECT power_score, governance, economy, innovation, openness,
               infrastructure, future_potential, risk, opportunity,
               social_order, human_capital
        FROM country_score_snapshots
        WHERE country_code = $1
        ORDER BY id DESC
        LIMIT 1
        `,
        [code]
      );

      const prevRow = prevSnap.rows[0];
      const prevPower = prevRow
        ? prevRow.power_score != null && Number.isFinite(Number(prevRow.power_score))
          ? Number(prevRow.power_score)
          : null
        : null;

      const { power_delta, power_trend } = powerTrendFromScores(prevPower, power_score);
      const power_note = buildPowerNote(power_trend, power_delta);

      const hist = await pool.query(
        `
        SELECT overall, power_score, risk, opportunity
        FROM country_score_snapshots
        WHERE country_code = $1
        ORDER BY id DESC
        LIMIT 5
        `,
        [code]
      );
      const chron = [...hist.rows].reverse();
      const overallSeries = [...chron.map((r) => Number(r.overall)), next.overall];
      const powerSeries = [
        ...chron.map((r) =>
          r.power_score != null && Number.isFinite(Number(r.power_score))
            ? Number(r.power_score)
            : Number(r.overall)
        ),
        power_score,
      ];
      const riskSeries = [...chron.map((r) => Number(r.risk)), next.risk];
      const opportunitySeries = [...chron.map((r) => Number(r.opportunity)), next.opportunity];

      const trends = computeCountrySnapshotTrends({
        overallSeries,
        powerSeries,
        riskSeries,
        opportunitySeries,
      });

      await pool.query(
        `
        INSERT INTO country_score_snapshots (
          country_code,
          snapshot_date,
          governance,
          social_order,
          economy,
          human_capital,
          infrastructure,
          innovation,
          openness,
          future_potential,
          overall,
          risk,
          opportunity,
          note,
          power_score,
          power_delta,
          power_trend,
          power_note,
          overall_trend_extended,
          power_trend_extended,
          risk_trend,
          opportunity_trend
        )
        VALUES (
          $1, NOW(),
          $2, $3, $4, $5, $6, $7, $8, $9,
          $10, $11, $12, $13,
          $14, $15, $16, $17,
          $18, $19, $20, $21
        )
        `,
        [
          code,
          next.governance,
          next.social_order,
          next.economy,
          next.human_capital,
          next.infrastructure,
          next.innovation,
          next.openness,
          next.future_potential,
          next.overall,
          next.risk,
          next.opportunity,
          "Evolution: event_consequences → country_scores → snapshot + trend inertia",
          power_score,
          power_delta,
          power_trend,
          power_note,
          trends.overall_trend_extended,
          trends.power_trend_extended,
          trends.risk_trend,
          trends.opportunity_trend,
        ]
      );

      await pool.query(
        `
        UPDATE country_scores SET
          governance = $1,
          social_order = $2,
          economy = $3,
          human_capital = $4,
          infrastructure = $5,
          innovation = $6,
          openness = $7,
          future_potential = $8,
          overall = $9,
          risk = $10,
          opportunity = $11,
          power_score = $12,
          power_classification = $13
        WHERE country_code = $14
        `,
        [
          next.governance,
          next.social_order,
          next.economy,
          next.human_capital,
          next.infrastructure,
          next.innovation,
          next.openness,
          next.future_potential,
          next.overall,
          next.risk,
          next.opportunity,
          power_score,
          power_classification,
          code,
        ]
      );

      console.log(
        `Updated ${code} → overall ${next.overall}, power ${power_score} (${power_trend})`
      );
      successfulCodes.add(code);
    }

    const appliedIds = active
      .filter((e) => successfulCodes.has(e.country_code))
      .map((e) => e.id);

    if (appliedIds.length > 0) {
      await pool.query(
        `
        UPDATE events
        SET applied_to_scores = TRUE, applied_at = NOW()
        WHERE id = ANY($1::int[])
        `,
        [appliedIds]
      );
      console.log(`Marked ${appliedIds.length} events applied (applied_at set).`);
    }

    stats.eventsMarkedApplied = appliedIds.length;
    stats.countriesUpdated = successfulCodes.size;

    console.log("Done.");
    return stats;
  } catch (err) {
    console.error(err);
    throw err;
  } finally {
    await pool.end();
  }
}

if (require.main === module) {
  runUpdateScores()
    .then(() => process.exit(0))
    .catch(() => process.exit(1));
}

module.exports = { runUpdateScores };
