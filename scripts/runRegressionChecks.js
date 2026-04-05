/**
 * Full regression: signal quality → idempotency → snapshot DB → trend → cache sample → build.
 */
const { execSync } = require("child_process");
const { ROOT } = require("./_regressionEnv.js");

function runNode(script) {
  console.log(`\n========== ${script} ==========\n`);
  execSync(`node ${script}`, {
    cwd: ROOT,
    stdio: "inherit",
    env: process.env,
  });
}

function main() {
  console.log("StateVerge runRegressionChecks.js\n");

  runNode("scripts/testSignalQuality.js");
  runNode("scripts/testIdempotency.js");
  runNode("scripts/testSnapshotUniqueness.js");
  runNode("scripts/testTrendStability.js");
  runNode("scripts/testCacheSample.js");

  console.log("\n========== npm run build ==========\n");
  execSync("npm run build", { cwd: ROOT, stdio: "inherit", env: process.env });

  console.log("\n========== All regression checks passed ==========\n");
  process.exit(0);
}

main();
