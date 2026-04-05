/**
 * Idempotency: updateScores twice → second run must see no pending events.
 * runDailyEvolution --dry-run twice → no crash (orchestration only).
 */
const path = require("path");
const { execSync } = require("child_process");
const { ROOT } = require("./_regressionEnv.js");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function main() {
  console.log("StateVerge testIdempotency.js\n");

  let out1 = "";
  let out2 = "";
  try {
    out1 = execSync("node scripts/updateScores.js", {
      cwd: ROOT,
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
    });
  } catch (e) {
    console.error("First updateScores failed:", e.stderr || e.message);
    process.exit(1);
  }

  try {
    out2 = execSync("node scripts/updateScores.js", {
      cwd: ROOT,
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
    });
  } catch (e) {
    console.error("Second updateScores failed:", e.stderr || e.message);
    process.exit(1);
  }

  assert(
    out2.includes("No pending events"),
    "Second run must report No pending events (events not double-applied)."
  );
  assert(out2.includes("Done."), "Second run must complete with Done.");
  console.log("✓ updateScores x2: second run is no-op on pending queue.");

  const dry1 = execSync("node scripts/runDailyEvolution.js --country=us --dry-run", {
    cwd: ROOT,
    encoding: "utf8",
  });
  const dry2 = execSync("node scripts/runDailyEvolution.js --country=us --dry-run", {
    cwd: ROOT,
    encoding: "utf8",
  });
  assert(dry1.includes("Daily evolution") || dry1.includes("dry-run"), "dry-run output");
  assert(dry2.includes("Daily evolution") || dry2.includes("dry-run"), "dry-run output");
  console.log("✓ runDailyEvolution --dry-run x2 completes (orchestration idempotent).");

  console.log("\nIdempotency checks passed.");
  process.exit(0);
}

main();
