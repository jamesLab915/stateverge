/**
 * Signal quality: event clustering, context-aware scaling, noise caps.
 * Used by scripts/updateScores.js only (no schema / page changes).
 */

function round2(x) {
  return Math.round(Number(x) * 100) / 100;
}

function normEventType(t) {
  return String(t || "")
    .trim()
    .toLowerCase();
}

/** Monotonic time key for ordering (ms). */
function eventTimeMs(e) {
  if (e.event_date != null) {
    const d = e.event_date instanceof Date ? e.event_date : new Date(e.event_date);
    const t = d.getTime();
    if (!Number.isNaN(t)) return t;
  }
  return Number(e.id) * 86400000;
}

const DAY_MS = 86400000;

function dayGapMs(a, b) {
  return Math.abs(eventTimeMs(a) - eventTimeMs(b));
}

/**
 * Split sorted (same type) events into clusters where each consecutive pair is within windowDays.
 * @param {object[]} sameTypeSorted oldest → newest
 * @param {number} windowDays
 */
function chunkByInterEventGap(sameTypeSorted, windowDays) {
  if (sameTypeSorted.length === 0) return [];
  const maxGap = windowDays * DAY_MS;
  const out = [];
  let cur = [sameTypeSorted[0]];
  for (let i = 1; i < sameTypeSorted.length; i++) {
    const prev = sameTypeSorted[i - 1];
    const next = sameTypeSorted[i];
    if (dayGapMs(prev, next) <= maxGap) {
      cur.push(next);
    } else {
      out.push(cur);
      cur = [next];
    }
  }
  out.push(cur);
  return out;
}

/**
 * Cluster unapplied events for one country: same normalized event_type, consecutive gaps ≤ windowDays.
 * @param {object[]} countryEvents
 * @param {{ windowDays?: number }} [opts]
 * @returns {object[][]} clusters of event rows
 */
function clusterCountryEvents(countryEvents, opts = {}) {
  const windowDays = opts.windowDays ?? 3;
  const byType = new Map();
  for (const e of countryEvents) {
    const k = normEventType(e.event_type);
    if (!byType.has(k)) byType.set(k, []);
    byType.get(k).push(e);
  }
  const clusters = [];
  for (const arr of byType.values()) {
    arr.sort((a, b) => eventTimeMs(a) - eventTimeMs(b));
    clusters.push(...chunkByInterEventGap(arr, windowDays));
  }
  return clusters;
}

/**
 * Same event_type / template has different effective amplitude by current ledger position.
 * @param {string} dimension
 * @param {number} value raw impact from consequence row
 * @param {object} scores country_scores row
 */
function applyContextScaleToDelta(dimension, value, scores) {
  const v = Number(value);
  if (!Number.isFinite(v) || Math.abs(v) < 1e-9) return v;
  const risk = Number(scores.risk ?? 50);
  const so = Number(scores.social_order ?? 50);
  const gov = Number(scores.governance ?? 50);
  const r = Math.max(0, Math.min(100, risk)) / 100;
  const sNorm = Math.max(0, Math.min(100, so)) / 100;
  const gNorm = Math.max(0, Math.min(100, gov)) / 100;

  let m = 1;
  const dim = String(dimension);
  if (dim === "risk") {
    m = 0.88 + 0.28 * r;
  } else if (dim === "opportunity") {
    m = 0.9 + 0.22 * (1 - r);
  } else if (dim === "social_order") {
    m = 0.86 + 0.28 * sNorm;
  } else if (dim === "governance") {
    m = 0.9 + 0.2 * gNorm;
  } else {
    const d = Number(scores[dim]);
    if (Number.isFinite(d)) {
      m = 1 + 0.14 * ((d - 50) / 50);
      m = Math.max(0.82, Math.min(1.18, m));
    }
  }
  return round2(v * m);
}

/** Diminishing returns when multiple same-type events merged in one apply batch. */
function clusterMergeFactor(clusterSize) {
  const n = Math.max(1, clusterSize);
  return Math.pow(n, -0.55);
}

function emptyDeltas() {
  return {
    governance: 0,
    social_order: 0,
    economy: 0,
    human_capital: 0,
    infrastructure: 0,
    innovation: 0,
    openness: 0,
    future_potential: 0,
    risk: 0,
    opportunity: 0,
  };
}

/**
 * Per-dimension cap + L1 cap on dimension deltas (noise control).
 * @param {Record<string, number>} g
 * @param {{ maxPerDim?: number, maxL1?: number }} opts
 */
function applyNoiseCapsToDimensionDeltas(g, opts = {}) {
  const maxPerDim = opts.maxPerDim ?? 4.5;
  const maxL1 = opts.maxL1 ?? 28;
  const keys = Object.keys(g);
  for (const k of keys) {
    if (k === "power_score") continue;
    const x = Number(g[k]);
    if (!Number.isFinite(x)) continue;
    let y = x;
    if (Math.abs(y) > maxPerDim) {
      const sign = y >= 0 ? 1 : -1;
      const over = Math.abs(y) - maxPerDim;
      y = sign * (maxPerDim + over * 0.35);
    }
    g[k] = round2(y);
  }
  let l1 = 0;
  for (const k of keys) {
    if (k === "power_score") continue;
    l1 += Math.abs(Number(g[k]) || 0);
  }
  if (l1 > maxL1 && l1 > 0) {
    const s = maxL1 / l1;
    for (const k of keys) {
      if (k === "power_score") continue;
      g[k] = round2(Number(g[k]) * s);
    }
  }
}

/**
 * Limit single-batch power_score channel adjustment.
 */
function applyPowerScoreNoiseCap(powerScoreAdjust, opts = {}) {
  const cap = opts.powerScoreCap ?? 3.5;
  let x = Number(powerScoreAdjust);
  if (!Number.isFinite(x)) return 0;
  if (Math.abs(x) > cap) {
    const sign = x >= 0 ? 1 : -1;
    const over = Math.abs(x) - cap;
    x = sign * (cap + over * 0.35);
  }
  return round2(x);
}

module.exports = {
  normEventType,
  clusterCountryEvents,
  applyContextScaleToDelta,
  clusterMergeFactor,
  emptyDeltas,
  applyNoiseCapsToDimensionDeltas,
  applyPowerScoreNoiseCap,
};
