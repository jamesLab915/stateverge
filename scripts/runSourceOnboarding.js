/**
 * Source Onboarding v1 — fetch curated RSS → adapter → Ingest Contract → INSERT events (if valid).
 * Does not bypass validation. Network required for fetch.
 *
 * Usage:
 *   node scripts/runSourceOnboarding.js --source=whitehouse [--limit=5] [--dry-run] [--country=us]
 *   node scripts/runSourceOnboarding.js --list
 *   node scripts/runSourceOnboarding.js --report-rejections   # alias: npm run ingest:report
 *
 * Full prod chain after successful insert: enrich:consequences → pipeline:run --score-only
 *   (see SOURCE_ONBOARDING_V1.md)
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const fs = require("fs");
const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");
const { parseRssItems } = require("../lib/onboarding/rssLite.js");
const {
  getCuratedSource,
  listCuratedSourceIds,
} = require("../lib/onboarding/curatedSources.js");
const { throughIngestContract } = require("../lib/ingest/adapters/throughIngestContract.js");
const { classifyIngestErrors } = require("../lib/ingest/classifyIngestRejection.js");
const {
  appendRejectionLog,
  previewRaw,
} = require("../lib/ingest/ingestRejectionLog.js");
const { insertValidatedEvent } = require("../lib/pipeline/sourceIngestionStep.js");

function parseArgs(argv) {
  let source = null;
  let limit = 5;
  let dryRun = false;
  let country = null;
  let reportPath = path.join(
    __dirname,
    "..",
    "data",
    "metrics",
    "onboarding",
    "last-report.json"
  );
  let list = false;
  let reportRejections = false;
  for (const a of argv) {
    if (a.startsWith("--source="))
      source = a.slice("--source=".length).trim().toLowerCase();
    else if (a.startsWith("--limit=")) {
      const n = parseInt(a.slice("--limit=".length), 10);
      if (Number.isFinite(n) && n > 0 && n <= 50) limit = n;
    } else if (a === "--dry-run" || a === "--trial") dryRun = true;
    else if (a.startsWith("--country="))
      country = a.slice("--country=".length).trim().toLowerCase() || null;
    else if (a.startsWith("--report="))
      reportPath = path.resolve(a.slice("--report=".length).trim());
    else if (a === "--list") list = true;
    else if (a === "--report-rejections") reportRejections = true;
  }
  return { source, limit, dryRun, country, reportPath, list, reportRejections };
}

function applyCountry(raw, c) {
  if (!c) return raw;
  return { ...raw, target_country_code: c };
}

async function fetchFeedXml(url) {
  const res = await fetch(url, {
    headers: {
      "User-Agent": "StateVerge-SourceOnboarding/1.0 (+https://github.com)",
      Accept: "application/rss+xml, application/xml, text/xml, */*",
    },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return res.text();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.list) {
    console.log("Curated source ids:", listCuratedSourceIds().join(", "));
    return;
  }

  if (args.reportRejections) {
    const { spawnSync } = require("child_process");
    spawnSync(
      process.execPath,
      [path.join(__dirname, "reportRejectedIngests.js"), "--json"],
      { stdio: "inherit", cwd: path.join(__dirname, "..") }
    );
    return;
  }

  if (!args.source) {
    console.error(
      "Usage: --source=whitehouse|fed_press|bbc_world [--limit=5] [--dry-run] [--country=xx]\n       --list | --report-rejections"
    );
    process.exit(2);
  }

  const src = getCuratedSource(args.source);
  if (!src) {
    console.error(`Unknown source: ${args.source}. Use --list.`);
    process.exit(2);
  }

  const effectiveCountry = args.country ?? src.defaultCountry;

  console.log(`Fetching ${src.displayName}\n  ${src.feedUrl}`);

  let xml;
  try {
    xml = await fetchFeedXml(src.feedUrl);
  } catch (e) {
    console.error("[onboarding] fetch failed:", e.message || e);
    process.exit(1);
  }

  const items = parseRssItems(xml).slice(0, args.limit);
  if (items.length === 0) {
    console.warn("No <item> entries parsed (feed format may have changed).");
  }

  let validatedOk = 0;
  let inserted = 0;
  let rejected = 0;
  /** @type {object[]} */
  const rejectionDetails = [];

  const pool = createPgPool();
  const client = args.dryRun ? null : await pool.connect();

  try {
    for (const item of items) {
      let raw = src.toRaw(item);
      raw = applyCountry(raw, effectiveCountry);

      const { validation } = throughIngestContract(raw);

      if (!validation.ok) {
        rejected++;
        const errs = validation.errors || [];
        const { categories, primary } = classifyIngestErrors(errs);
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: `onboarding:${src.id}`,
          ok: false,
          errors: errs,
          categories,
          primaryCategory: primary,
          rawPreview: previewRaw(raw),
        });
        rejectionDetails.push({
          title: item.title,
          errors: errs,
          primaryCategory: primary,
        });
        console.log(`[reject] ${primary} ${errs.join(";")} :: ${(item.title || "").slice(0, 70)}`);
        continue;
      }

      const val = validation.value;
      if (
        val.source_type == null ||
        val.confidence == null ||
        !val.source_name ||
        !val.source_url
      ) {
        rejected++;
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: `onboarding:${src.id}`,
          ok: false,
          errors: ["internal_contract_breach_missing_provenance"],
          categories: ["other"],
          primaryCategory: "other",
          rawPreview: previewRaw(raw),
        });
        rejectionDetails.push({
          title: item.title,
          errors: ["internal_contract_breach_missing_provenance"],
        });
        continue;
      }

      validatedOk++;
      if (args.dryRun) {
        console.log(
          `[dry-run] ok layer=${validation.layer} ${(val.title || "").slice(0, 80)}`
        );
        continue;
      }

      try {
        const id = await insertValidatedEvent(client, val);
        inserted++;
        console.log(`[insert] id=${id} ${(val.title || "").slice(0, 80)}`);
      } catch (e) {
        rejected++;
        const msg = e instanceof Error ? e.message : String(e);
        appendRejectionLog({
          ts: new Date().toISOString(),
          adapter: `onboarding:${src.id}`,
          ok: false,
          errors: ["db_insert_failed", msg],
          categories: ["database"],
          primaryCategory: "database",
          rawPreview: previewRaw(raw),
        });
        rejectionDetails.push({ title: item.title, errors: ["db_insert_failed", msg] });
        console.error(`[reject] db ${msg}`);
      }
    }
  } finally {
    if (client) client.release();
    await pool.end();
  }

  const stableIntoEvents =
    items.length > 0 &&
    validatedOk === items.length &&
    rejected === 0 &&
    (args.dryRun || inserted === validatedOk);

  const report = {
    schema: "source_onboarding_report_v1",
    generated_at: new Date().toISOString(),
    source_id: src.id,
    display_name: src.displayName,
    feed_url: src.feedUrl,
    source_type: src.source_type,
    adapter: src.adapter,
    effective_country: effectiveCountry,
    field_gaps: src.fieldGaps,
    fetch: {
      items_parsed: items.length,
      limit: args.limit,
      dry_run: args.dryRun,
    },
    results: {
      validated_ok: validatedOk,
      inserted,
      rejected,
      stable_into_events: stableIntoEvents,
    },
    rejection_sample: rejectionDetails.slice(0, 20),
    production_chain_hint: [
      "npm run enrich:consequences -- --limit=<N>",
      "npm run pipeline:run -- --score-only",
      "npm run report:pipeline-health",
    ],
  };

  fs.mkdirSync(path.dirname(args.reportPath), { recursive: true });
  fs.writeFileSync(args.reportPath, JSON.stringify(report, null, 2), "utf8");
  console.log(`\nReport written: ${args.reportPath}`);
  console.log(
    `Done. validated_ok=${validatedOk} inserted=${inserted} rejected=${rejected} dryRun=${args.dryRun}`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
