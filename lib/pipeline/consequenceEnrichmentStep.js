/**
 * Consequence enrichment step — shared by scripts/runConsequenceEnrichment.js and pipeline.
 */

const { createPgPool } = require("../db/pgPool.js");
const { buildConsequenceRows } = require("../eventConsequenceMapping.js");
const {
  buildCountryConsequenceRowsFromTemplate,
  buildDefaultCountryConsequenceRows,
} = require("../evolution/consequenceTemplates.js");
const { attachPrimaryActorIfPresent } = require("../eventAttachments.js");

function buildActorPayloadFromEventRow(e) {
  const base = {
    event_type: e.event_type,
    impact_direction: e.impact_direction,
    impact_strength: e.impact_strength,
  };
  let ep = e.enrichment_payload;
  if (ep == null) return base;
  if (typeof ep === "string") {
    try {
      ep = JSON.parse(ep);
    } catch {
      return base;
    }
  }
  if (
    ep &&
    typeof ep === "object" &&
    ep.primary_actor &&
    typeof ep.primary_actor === "object" &&
    ep.primary_actor.name
  ) {
    return { ...base, primary_actor: ep.primary_actor };
  }
  return base;
}

function buildConsequenceRowList(e) {
  let rows = buildCountryConsequenceRowsFromTemplate(e);
  if (rows.length === 0) rows = buildConsequenceRows(e);
  if (rows.length === 0) rows = buildDefaultCountryConsequenceRows(e);
  return rows;
}

/**
 * @param {string[]} argv
 */
function parseEnrichmentArgs(argv) {
  let limit = null;
  let dryRun = false;
  for (const a of argv) {
    if (a.startsWith("--limit=")) {
      const n = parseInt(a.slice("--limit=".length), 10);
      if (Number.isFinite(n) && n > 0) limit = n;
    } else if (a === "--dry-run") dryRun = true;
  }
  return { limit, dryRun };
}

/**
 * @param {{
 *   limit?: number | null,
 *   dryRun?: boolean,
 * }} opts
 */
async function runConsequenceEnrichmentStep(opts) {
  const limit = opts.limit != null ? opts.limit : null;
  const dryRun = Boolean(opts.dryRun);
  const pool = createPgPool();

  const q = `
    SELECT e.* FROM events e
    WHERE NOT EXISTS (
      SELECT 1 FROM event_consequences ec WHERE ec.event_id = e.id
    )
    ORDER BY e.id ASC
    ${limit != null ? "LIMIT $1" : ""}
  `;
  const { rows: events } = await pool.query(
    q,
    limit != null ? [limit] : []
  );

  let consequenceRows = 0;
  let actorsAttached = 0;
  const client = await pool.connect();

  try {
    for (const e of events) {
      const country = e.country_code;
      const eventId = e.id;
      const rows = buildConsequenceRowList(e);

      if (!dryRun) {
        for (const r of rows) {
          await client.query(
            `
            INSERT INTO event_consequences (
              event_id, target_country_code, dimension, impact_value,
              time_horizon, confidence, explanation
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            `,
            [
              eventId,
              country,
              r.dimension,
              r.impact_value,
              r.time_horizon,
              r.confidence,
              r.explanation,
            ]
          );
          consequenceRows++;
        }
      } else {
        consequenceRows += rows.length;
      }

      const payload = buildActorPayloadFromEventRow(e);
      if (payload.primary_actor) {
        const { rows: ex } = await client.query(
          `SELECT 1 FROM event_actors WHERE event_id = $1 LIMIT 1`,
          [eventId]
        );
        if (ex.length === 0) {
          if (dryRun) actorsAttached++;
          else {
            await attachPrimaryActorIfPresent(
              client,
              eventId,
              String(country),
              payload.primary_actor
            );
            actorsAttached++;
          }
        }
      }
    }
  } finally {
    client.release();
    await pool.end();
  }

  return {
    step: "enrichment",
    eventsProcessed: events.length,
    consequenceRows,
    actorsAttached,
    dryRun,
  };
}

module.exports = {
  runConsequenceEnrichmentStep,
  parseEnrichmentArgs,
};
