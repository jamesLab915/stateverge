const { execSync } = require("child_process");

try {
  console.log("Step 1: Generating AI events...");
  execSync("node scripts/autoEvents.js", { stdio: "inherit" });

  console.log("Step 2: Backfilling consequences (no-op if already present)...");
  try {
    execSync("node scripts/backfillEventConsequences.js", { stdio: "inherit" });
  } catch {
    console.log("backfill skipped or failed — check migration.");
  }

  console.log("Step 3: Updating scores & snapshots...");
  execSync("node scripts/updateScores.js", { stdio: "inherit" });

  console.log("Step 4: Refreshing cached insights (optional)...");
  try {
    execSync("node scripts/generateCausalSummaries.js", { stdio: "inherit" });
    execSync("node scripts/generateScenarios.js", { stdio: "inherit" });
  } catch {
    console.log("Insight scripts skipped (no OpenAI key or tables missing).");
  }

  console.log("Evolution complete.");
} catch (error) {
  console.error("Evolution failed:", error.message);
}
