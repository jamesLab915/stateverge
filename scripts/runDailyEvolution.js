/**
 * Daily evolution loop: orchestrates existing scripts only (no core logic changes).
 * ingest → backfill consequences → updateScores → idempotent snapshot → cache invalidation
 *
 * Usage:
 *   node scripts/runDailyEvolution.js [--all] [--country=us,cn] [--dry-run]
 *
 * Env (optional):
 *   EVOLUTION_COUNTRIES — also used by autoEvents.js when set by this script
 *
 * Cron example:
 *   0 6 * * * cd /path/to/stateverge && node scripts/runDailyEvolution.js --all
 */

require("dotenv").config({ path: ".env.local", override: true });

const path = require("path");
const { execSync } = require("child_process");
const { createPgPool } = require("../lib/db/pgPool.js");
const {
  computePowerScore,
  classifyPower,
  powerTrendFromScores,
  buildPowerNote,
} = require("../lib/powerMath.js");

const ROOT = path.join(__dirname, "..");

const pool = createPgPool();

function parseArgs() {
  const out = {
    all: false,
    countries: null,
    dryRun: false,
  };
  for (let i = 2; i < process.argv.length; i++) {
    const a = process.argv[i];
    if (a === "--dry-run") out.dryRun = true;
    else if (a === "--all") out.all = true;
    else if (a.startsWith("--country=")) {
      out.countries = a
        .slice("--country=".length)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
  }
  return out;
}

async function resolveTargetCodes(args) {
  if (args.countries && args.countries.length > 0) return args.countries;
  const { rows } = await pool.query(`SELECT code FROM countries ORDER BY code`);
  return rows.map((r) => r.code);
}

function execNodeScript(scriptName, extraEnv, dryRun) {
  const cmd = `node scripts/${scriptName}`;
  if (dryRun) {
    console.log(`[dry-run] would run: ${cmd}`, extraEnv || "");
    return;
  }
  execSync(cmd, {
    cwd: ROOT,
    stdio: "inherit",
    env: { ...process.env, ...extraEnv },
  });
}

/**
 * Same row shape as scripts/saveSnapshot.js for one country; skips if a snapshot already exists for local CURRENT_DATE.
 */
async function ensureSnapshotTodayIfMissing(countryCode, dryRun) {
  if (dryRun) {
    console.log(`[dry-run] ensureSnapshotTodayIfMissing(${countryCode})`);
    return;
  }

  const exists = await pool.query(
    `
    SELECT 1
    FROM country_score_snapshots
    WHERE country_code = $1
      AND snapshot_date::date = CURRENT_DATE
    LIMIT 1
    `,
    [countryCode]
  );
  if (exists.rows.length > 0) {
    console.log(`Snapshot already exists for ${countryCode} today — skip.`);
    return;
  }

  const { rows: scoreRows } = await pool.query(
    `SELECT * FROM country_scores WHERE country_code = $1`,
    [countryCode]
  );
  if (scoreRows.length === 0) {
    console.log(`No country_scores for ${countryCode} — skip snapshot.`);
    return;
  }

  const s = scoreRows[0];
  const power_score = computePowerScore(s);
  const power_classification = classifyPower({ ...s, power_score });

  await pool.query(
    `
    UPDATE country_scores
    SET power_score = $1, power_classification = $2
    WHERE country_code = $3
    `,
    [power_score, power_classification, countryCode]
  );

  const prevSnap = await pool.query(
    `
    SELECT power_score FROM country_score_snapshots
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT 1
    `,
    [countryCode]
  );

  const prevRow = prevSnap.rows[0];
  const prevPower = prevRow
    ? prevRow.power_score != null && Number.isFinite(Number(prevRow.power_score))
      ? Number(prevRow.power_score)
      : computePowerScore(prevRow)
    : null;

  const { power_delta, power_trend } = powerTrendFromScores(prevPower, power_score);
  const power_note = buildPowerNote(power_trend, power_delta);

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
      power_note
    )
    SELECT
      country_code,
      CURRENT_DATE,
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
      'Daily evolution: ledger snapshot (no new events path)',
      $2::double precision,
      $3::double precision,
      $4,
      $5
    FROM country_scores
    WHERE country_code = $1
    RETURNING id, country_code, snapshot_date
    `,
    [countryCode, power_score, power_delta, power_trend, power_note]
  );

  console.log(`Snapshot written for ${countryCode} (today was missing).`);
}

/**
 * Drops cached AI/polished payloads so next HTTP request rebuilds from rule engines.
 */
async function invalidateEvolutionCaches(targetCodes, dryRun) {
  if (dryRun) {
    console.log("[dry-run] would invalidate evolution caches for:", targetCodes.join(", "));
    return;
  }

  const { rows: allRows } = await pool.query(`SELECT code FROM countries ORDER BY code`);
  const allCodes = allRows.map((r) => r.code);
  const keys = new Set();

  for (const c of targetCodes) {
    keys.add(`evolution-summary:${c}`);
    keys.add(`narrative-graph:${c}`);
  }

  for (const a of targetCodes) {
    for (const b of allCodes) {
      if (a === b) continue;
      keys.add(`evolution-contrast:${a}-${b}`);
      keys.add(`narrative-graph-compare:${a}-${b}`);
    }
  }

  const arr = [...keys];
  const del = await pool.query(
    `DELETE FROM ai_insights_cache WHERE cache_key = ANY($1::text[])`,
    [arr]
  );
  console.log(
    `Invalidated evolution caches: ${del.rowCount ?? 0} row(s) removed (${arr.length} key(s) targeted).`
  );
}

async function main() {
  const args = parseArgs();
  const targetCodes = await resolveTargetCodes(args);

  console.log("=== Daily evolution ===");
  console.log("Target countries:", targetCodes.join(", "));
  console.log("Dry run:", args.dryRun);

  const evoCountries = targetCodes.join(",");

  console.log("\n[1] Ingest events (autoEvents.js)…");
  execNodeScript("autoEvents.js", { EVOLUTION_COUNTRIES: evoCountries }, args.dryRun);

  console.log("\n[2] Backfill consequences (backfillEventConsequences.js)…");
  execNodeScript("backfillEventConsequences.js", {}, args.dryRun);

  console.log("\n[3] Apply scores from events (updateScores.js)…");
  execNodeScript("updateScores.js", {}, args.dryRun);

  console.log("\n[4] Ensure snapshot for today (idempotent per country)…");
  for (const code of targetCodes) {
    await ensureSnapshotTodayIfMissing(code, args.dryRun);
  }

  console.log("\n[5] Invalidate narrative / contrast / graph caches…");
  await invalidateEvolutionCaches(targetCodes, args.dryRun);

  console.log("\n=== Done ===");
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
