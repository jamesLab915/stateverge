/**
 * Observability Layer v1 — append-only JSONL for evolution pipeline runs.
 * Path: data/metrics/evolution/runs-YYYY-MM-DD.jsonl
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

function getProjectRoot() {
  return path.join(__dirname, "..", "..");
}

function getEvolutionMetricsDir() {
  return path.join(getProjectRoot(), "data", "metrics", "evolution");
}

/**
 * @param {Record<string, unknown>} record
 */
function appendEvolutionPipelineRun(record) {
  const dir = getEvolutionMetricsDir();
  fs.mkdirSync(dir, { recursive: true });
  const day = new Date().toISOString().slice(0, 10);
  const file = path.join(dir, `runs-${day}.jsonl`);
  fs.appendFileSync(file, JSON.stringify(record) + "\n", "utf8");
}

/**
 * @param {{
 *   report: Array<{ name?: string, ok?: boolean, error?: string, startedAt?: string, finishedAt?: string, stats?: Record<string, unknown> }>,
 *   argv: string[],
 *   pipelineArgs: { dryRun?: boolean, runIngest?: boolean, runEnrich?: boolean, runScore?: boolean },
 *   exitCode: number,
 * }} p
 */
function buildPipelineRunRecord(p) {
  const mode = inferPipelineMode(p.pipelineArgs);
  const steps = (p.report || []).map((s) => ({
    name: s.name,
    ok: s.ok,
    error: s.error || null,
    startedAt: s.startedAt || null,
    finishedAt: s.finishedAt || null,
    stats: s.stats || null,
  }));
  return {
    schema: "evolution_pipeline_run_v1",
    ts: new Date().toISOString(),
    hostname: os.hostname(),
    pid: process.pid,
    argv: p.argv,
    mode,
    dryRun: Boolean(p.pipelineArgs.dryRun),
    overallOk: p.exitCode === 0,
    exitCode: p.exitCode,
    steps,
  };
}

/**
 * @param {{ runIngest?: boolean, runEnrich?: boolean, runScore?: boolean }} a
 */
function inferPipelineMode(a) {
  const ri = Boolean(a.runIngest);
  const re = Boolean(a.runEnrich);
  const rs = Boolean(a.runScore);
  if (ri && re && rs) return "all";
  if (ri && !re && !rs) return "ingest";
  if (!ri && re && !rs) return "enrich";
  if (!ri && !re && rs) return "score";
  if (ri || re || rs) return "partial";
  return "none";
}

/**
 * @param {string} [globDir]
 * @returns {string[]} absolute paths to runs-*.jsonl files, newest day first
 */
function listRunLogFiles(globDir) {
  const dir = globDir || getEvolutionMetricsDir();
  if (!fs.existsSync(dir)) return [];
  const names = fs.readdirSync(dir).filter((n) => /^runs-\d{4}-\d{2}-\d{2}\.jsonl$/.test(n));
  return names
    .sort()
    .reverse()
    .map((n) => path.join(dir, n));
}

/**
 * @param {string} filePath
 * @returns {Record<string, unknown>[]}
 */
function readJsonlFile(filePath) {
  if (!fs.existsSync(filePath)) return [];
  const text = fs.readFileSync(filePath, "utf8");
  const out = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t));
    } catch {
      /* skip */
    }
  }
  return out;
}

module.exports = {
  appendEvolutionPipelineRun,
  buildPipelineRunRecord,
  getEvolutionMetricsDir,
  inferPipelineMode,
  listRunLogFiles,
  readJsonlFile,
};
