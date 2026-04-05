/**
 * Shared helpers for real-source adapters (v1).
 * Feeds often omit impact fields; v1 uses explicit neutral placeholders until an enricher assigns them.
 */

/** @type {const} */
const DEFAULT_IMPACT_DIRECTION = "positive";

/** Neutral default when source has no directional signal (documented in REAL_SOURCE_ADAPTERS_V1). */
const DEFAULT_IMPACT_STRENGTH = 5;

/**
 * @param {unknown} d ISO string, YYYY-MM-DD, or Date
 * @returns {string|undefined}
 */
function pickEventDate(d) {
  if (d === undefined || d === null || d === "") return undefined;
  if (d instanceof Date && !Number.isNaN(d.getTime()))
    return d.toISOString().slice(0, 10);
  const s = String(d).trim();
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(s);
  if (m) return m[1];
  const x = new Date(s);
  if (!Number.isNaN(x.getTime())) return x.toISOString().slice(0, 10);
  return undefined;
}

/**
 * @param {string} s
 * @param {number} max
 */
function clip(s, max) {
  const t = String(s ?? "").trim();
  return t.length > max ? t.slice(0, max) : t;
}

module.exports = {
  DEFAULT_IMPACT_DIRECTION,
  DEFAULT_IMPACT_STRENGTH,
  pickEventDate,
  clip,
};
