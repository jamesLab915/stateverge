/**
 * Calibration spot-check: prints rule-layer outputs for manual review vs DB.
 * Usage: node scripts/calibrationSpotcheck.js [countryCode] [leftCode] [rightCode]
 * Defaults: us cn us-cn
 */
require("dotenv").config({ path: ".env.local" });
const { createPgPool } = require("../lib/db/pgPool.js");
const { buildCountryEvolutionNarrative } = require("../lib/evolution/narrativeEngine.js");
const { buildCountryEvolutionContrast } = require("../lib/evolution/contrastEngine.js");
const {
  buildCountryNarrativeGraph,
  buildCompareNarrativeGraph,
  keyPathSupported,
} = require("../lib/evolution/narrativeGraph.js");

const pool = createPgPool();

async function main() {
  const code = process.argv[2] || "us";
  const left = process.argv[3] || "us";
  const right = process.argv[4] || "cn";

  console.log("=== Snapshots (verify narrative 'from ~ to ~') ===");
  const sn = await pool.query(
    `SELECT overall, snapshot_date FROM country_score_snapshots WHERE country_code = $1 ORDER BY id DESC LIMIT 5`,
    [code]
  );
  console.table(sn.rows);

  console.log("\n=== Evolution narrative (rule) ===");
  const nar = await buildCountryEvolutionNarrative(pool, code);
  console.log("direction:", nar.direction);
  console.log("summary:\n", nar.summary);
  console.log("turning_points[0]:", nar.turning_points[0] || null);

  console.log("\n=== Evolution contrast (rule) ===");
  const ctr = await buildCountryEvolutionContrast(pool, left, right);
  console.log("summary:\n", ctr.summary);
  console.log("bottom_line:\n", ctr.bottom_line);

  console.log("\n=== Narrative graph key_paths trace ===");
  const g = await buildCountryNarrativeGraph(pool, code);
  for (const kp of g.key_paths) {
    const ok = keyPathSupported(kp.path, g.edges);
    console.log(ok ? "OK " : "BAD", kp.path.join(" -> "), "|", kp.why_it_matters.slice(0, 80));
  }

  const gc = await buildCompareNarrativeGraph(pool, left, right);
  console.log("\n=== Compare graph key_paths trace ===");
  for (const kp of gc.key_paths) {
    const ok = keyPathSupported(kp.path, gc.edges);
    console.log(ok ? "OK " : "BAD", kp.path.join(" -> "));
  }

  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
