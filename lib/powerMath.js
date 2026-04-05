/**
 * StateVerge Power Tracking v1 — single source of truth for weights & rules.
 * Used by Node scripts (require) and re-exported from lib/power.ts for the app.
 */

const POWER_WEIGHTS = Object.freeze({
  economy: 0.25,
  innovation: 0.2,
  infrastructure: 0.15,
  governance: 0.15,
  openness: 0.1,
  future_potential: 0.15,
});

function n(x, def = 0) {
  const v = Number(x);
  return Number.isFinite(v) ? v : def;
}

/**
 * @param {Record<string, unknown>} row — dimension scores 0–100
 * @returns {number}
 */
function computePowerScore(row) {
  const score =
    POWER_WEIGHTS.economy * n(row.economy) +
    POWER_WEIGHTS.innovation * n(row.innovation) +
    POWER_WEIGHTS.infrastructure * n(row.infrastructure) +
    POWER_WEIGHTS.governance * n(row.governance) +
    POWER_WEIGHTS.openness * n(row.openness) +
    POWER_WEIGHTS.future_potential * n(row.future_potential);
  return Math.round(score * 100) / 100;
}

/**
 * Rule-based v1 classification (order matters).
 * @param {Record<string, unknown>} row — must include dimensions; optional power_score
 */
function classifyPower(row) {
  const power_score = n(
    row.power_score !== undefined && row.power_score !== null
      ? row.power_score
      : computePowerScore(row)
  );
  const risk = n(row.risk);
  const future_potential = n(row.future_potential);
  const innovation = n(row.innovation);
  const economy = n(row.economy);
  const infrastructure = n(row.infrastructure);

  if (power_score >= 85) return "Established Power";
  if (power_score >= 78 && future_potential >= 80) return "Rising Power";
  if (risk >= 60 && power_score < 70) return "Fragile Power";
  if (innovation >= 85) return "Innovation Power";
  if (economy >= 80 && infrastructure >= 80) return "Strategic Power";
  return "Regional Power";
}

/**
 * @param {number|null|undefined} prevPower
 * @param {number|null|undefined} nextPower
 * @returns {{ power_delta: number|null, power_trend: string }}
 */
function powerTrendFromScores(prevPower, nextPower) {
  if (
    prevPower === null ||
    prevPower === undefined ||
    nextPower === null ||
    nextPower === undefined ||
    !Number.isFinite(Number(prevPower)) ||
    !Number.isFinite(Number(nextPower))
  ) {
    return { power_delta: null, power_trend: "stable" };
  }
  const delta = Math.round((Number(nextPower) - Number(prevPower)) * 100) / 100;
  if (delta > 1) return { power_delta: delta, power_trend: "rising" };
  if (delta < -1) return { power_delta: delta, power_trend: "declining" };
  return { power_delta: delta, power_trend: "stable" };
}

function buildPowerNote(power_trend, power_delta) {
  if (power_delta === null || power_delta === undefined) {
    return "Power baseline recorded (no prior snapshot).";
  }
  const dir =
    power_delta > 0 ? "up" : power_delta < 0 ? "down" : "flat";
  return `Power ${dir} ${Math.abs(power_delta)} vs prior snapshot (${power_trend}).`;
}

module.exports = {
  POWER_WEIGHTS,
  computePowerScore,
  classifyPower,
  powerTrendFromScores,
  buildPowerNote,
};
