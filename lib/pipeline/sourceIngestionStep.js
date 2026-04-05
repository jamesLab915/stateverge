/**
 * Source ingestion step — shared by scripts/runSourceIngestion.js and evolution pipeline.
 */

const { createPgPool } = require("../db/pgPool.js");
const {
  rawEventFromOfficialRecord,
  sampleOfficialRecord,
  rawEventFromInstitutionalRecord,
  sampleInstitutionalRecord,
  rawEventFromReputableMediaRecord,
  sampleReputableMediaRecord,
  throughIngestContract,
} = require("../ingest/adapters/index.js");
const { classifyIngestErrors } = require("../ingest/classifyIngestRejection.js");
const {
  appendRejectionLog,
  previewRaw,
} = require("../ingest/ingestRejectionLog.js");

function defaultAdapterFactories() {
  return {
    official: () => rawEventFromOfficialRecord(sampleOfficialRecord()),
    institutional: () =>
      rawEventFromInstitutionalRecord(sampleInstitutionalRecord()),
    reputable_media: () =>
      rawEventFromReputableMediaRecord(sampleReputableMediaRecord()),
  };
}

function parseCommaList(s) {
  return s
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * @param {string[]} argv
 */
function parseSourceIngestArgs(argv) {
  let adapters = null;
  let country = process.env.INGEST_DEFAULT_COUNTRY || null;
  let dryRun = false;
  for (const a of argv) {
    if (a.startsWith("--adapters="))
      adapters = parseCommaList(a.slice("--adapters=".length));
    else if (a.startsWith("--country="))
      country = a.slice("--country=".length).trim().toLowerCase() || null;
    else if (a === "--dry-run") dryRun = true;
  }
  if (!adapters && process.env.INGEST_ADAPTERS)
    adapters = parseCommaList(process.env.INGEST_ADAPTERS);
  if (!adapters || adapters.length === 0)
    adapters = ["official", "institutional", "reputable_media"];
  return { adapters, country, dryRun };
}

function applyDefaultCountry(raw, country) {
  if (!country) return raw;
  return { ...raw, target_country_code: country };
}

/**
 * @param {import("pg").PoolClient} client
 * @param {object} v
 */
async function insertValidatedEvent(client, v) {
  const parentId = v.parent_event_id != null ? v.parent_event_id : null;
  const cols = [
    "country_code",
    "event_type",
    "title",
    "summary",
    "event_date",
    "impact_direction",
    "impact_strength",
    "source_name",
    "source_url",
    "source_type",
    "confidence",
    "applied_to_scores",
  ];
  const vals = [
    v.country_code,
    v.event_type,
    v.title,
    v.summary,
    v.event_date,
    v.impact_direction,
    v.impact_strength,
    v.source_name,
    v.source_url,
    v.source_type,
    v.confidence,
    false,
  ];
  if (parentId != null) {
    cols.push("parent_event_id");
    vals.push(parentId);
  }
  const ph = vals.map((_, i) => `$${i + 1}`).join(", ");
  const sql = `INSERT INTO events (${cols.join(", ")}) VALUES (${ph}) RETURNING id`;
  const res = await client.query(sql, vals);
  return res.rows[0].id;
}

/**
 * @param {{
 *   adapters?: string[],
 *   country?: string | null,
 *   dryRun?: boolean,
 * }} opts
 */
async function runSourceIngestionStep(opts) {
  const adapters = opts.adapters || [
    "official",
    "institutional",
    "reputable_media",
  ];
  const country = opts.country ?? null;
  const dryRun = Boolean(opts.dryRun);
  const pool = createPgPool();

  const factories = defaultAdapterFactories();
  let validatedOk = 0;
  let inserted = 0;
  let rejected = 0;
  let skippedUnknownAdapter = 0;

  /** @type {import("pg").PoolClient | null} */
  let client = dryRun ? null : await pool.connect();
  try {
    for (const name of adapters) {
      const factory = factories[name];
      if (!factory) {
        skippedUnknownAdapter++;
        continue;
      }
      let raw = factory();
      raw = applyDefaultCountry(raw, country);

      const { validation } = throughIngestContract(raw);

      if (!validation.ok) {
        rejected++;
        const errs = validation.errors || [];
        const { categories, primary } = classifyIngestErrors(errs);
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: name,
          ok: false,
          errors: errs,
          categories,
          primaryCategory: primary,
          rawPreview: previewRaw(raw),
        });
        console.log(
          `[reject] ${name} primary=${primary} errors=${errs.join(";")}`
        );
        continue;
      }

      const val = validation.value;
      if (
        val.source_type == null ||
        val.confidence == null ||
        !val.source_name ||
        !val.source_url
      ) {
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: name,
          ok: false,
          errors: ["internal_contract_breach_missing_provenance"],
          categories: ["other"],
          primaryCategory: "other",
          rawPreview: previewRaw(raw),
        });
        rejected++;
        console.log(`[reject] ${name} missing provenance after validation`);
        continue;
      }

      validatedOk++;
      if (dryRun) {
        console.log(
          `[dry-run] ${name} layer=${validation.layer} would insert title=${val.title?.slice(0, 60)}`
        );
        continue;
      }

      try {
        const id = await insertValidatedEvent(client, val);
        inserted++;
        console.log(
          `[insert] ${name} id=${id} layer=${validation.layer} country=${val.country_code}`
        );
      } catch (e) {
        rejected++;
        const msg = e instanceof Error ? e.message : String(e);
        const errs = ["db_insert_failed"];
        const { categories, primary } = classifyIngestErrors(errs);
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: name,
          ok: false,
          errors: [...errs, msg],
          categories,
          primaryCategory: primary,
          rawPreview: previewRaw(raw),
        });
        console.error(`[reject] ${name} db: ${msg}`);
      }
    }
  } finally {
    if (client) client.release();
    await pool.end();
  }

  return {
    step: "ingestion",
    validatedOk,
    inserted,
    rejected,
    skippedUnknownAdapter,
    dryRun,
  };
}

module.exports = {
  runSourceIngestionStep,
  parseSourceIngestArgs,
  defaultAdapterFactories,
  insertValidatedEvent,
};
