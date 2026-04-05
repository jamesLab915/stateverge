/**
 * Append-only JSONL log for rejected ingest attempts (runner v1).
 */

const fs = require("fs");
const path = require("path");

function getProjectRoot() {
  return path.join(__dirname, "..", "..");
}

function getRejectionLogPath() {
  return path.join(getProjectRoot(), "data", "ingest_rejections.jsonl");
}

function ensureDataDir() {
  const dir = path.join(getProjectRoot(), "data");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

/**
 * @param {Record<string, unknown>} entry
 */
function appendRejectionLog(entry) {
  ensureDataDir();
  const line = JSON.stringify(entry) + "\n";
  fs.appendFileSync(getRejectionLogPath(), line, "utf8");
}

/**
 * @returns {Record<string, unknown>[]}
 */
function readRejectionLogLines() {
  const p = getRejectionLogPath();
  if (!fs.existsSync(p)) return [];
  const text = fs.readFileSync(p, "utf8");
  const out = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t));
    } catch {
      /* skip corrupt line */
    }
  }
  return out;
}

/**
 * @param {unknown} raw
 */
function previewRaw(raw) {
  if (raw == null || typeof raw !== "object") return {};
  const o = /** @type {Record<string, unknown>} */ ({ ...raw });
  for (const k of Object.keys(o)) {
    const v = o[k];
    if (typeof v === "string" && v.length > 240) o[k] = v.slice(0, 240) + "…";
  }
  return o;
}

module.exports = {
  getRejectionLogPath,
  appendRejectionLog,
  readRejectionLogLines,
  previewRaw,
};
