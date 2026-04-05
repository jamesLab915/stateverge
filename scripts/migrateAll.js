const { execSync } = require("child_process");
const path = require("path");

const root = path.join(__dirname, "..");

try {
  execSync("node scripts/migrateEvolutionEngine.js", { cwd: root, stdio: "inherit" });
  console.log("db:migrate-all complete (single unified SQL).");
} catch (e) {
  console.error(e);
  process.exit(1);
}
