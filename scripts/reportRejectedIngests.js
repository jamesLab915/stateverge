/**
 * Summarize data/ingest_rejections.jsonl — counts by category and error code.
 *
 * Usage: node scripts/reportRejectedIngests.js [--json]
 */
const path = require("path");
const { readRejectionLogLines } = require("../lib/ingest/ingestRejectionLog.js");
const { errorCodeToCategory } = require("../lib/ingest/classifyIngestRejection.js");

function main() {
  const json = process.argv.includes("--json");
  const lines = readRejectionLogLines();
  const total_rejected = lines.length;

  /** @type {Record<string, number>} */
  const by_primary_category = {};
  /** @type {Record<string, number>} */
  const by_category = {};
  /** @type {Record<string, number>} */
  const by_error_code = {};

  for (const row of lines) {
    const primary = String(row.primaryCategory || "other");
    by_primary_category[primary] = (by_primary_category[primary] || 0) + 1;

    const errs = Array.isArray(row.errors) ? row.errors : [];
    for (const code of errs) {
      if (String(code).length > 200) continue;
      by_error_code[String(code)] = (by_error_code[String(code)] || 0) + 1;
      const cat = errorCodeToCategory(String(code));
      by_category[cat] = (by_category[cat] || 0) + 1;
    }
  }

  const out = {
    generated_at: new Date().toISOString(),
    log_path: path.join(__dirname, "..", "data", "ingest_rejections.jsonl"),
    total_rejected,
    by_primary_category,
    by_category,
    by_error_code,
  };

  if (json) {
    console.log(JSON.stringify(out, null, 2));
  } else {
    console.log("StateVerge — rejected ingests\n");
    console.log(JSON.stringify(out, null, 2));
  }
}

main();
