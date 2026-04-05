/**
 * Trend inertia from recent numeric snapshots (deterministic labels).
 */

/**
 * @param {number[]} series oldest → newest (length >= 2 for trend; 1 returns stable/insufficient)
 * @param {string} label
 */
function classifySeries(series, label = "metric") {
  const s = (series || []).map((x) => Number(x)).filter((x) => Number.isFinite(x));
  if (s.length < 2) {
    return { label: "stable", detail: `${label}: insufficient history` };
  }

  const n = s.length;
  const first = s[0];
  const last = s[n - 1];
  const delta = last - first;
  const absD = Math.abs(delta);

  const stepDeltas = [];
  for (let i = 1; i < s.length; i++) stepDeltas.push(s[i] - s[i - 1]);
  const meanStep = stepDeltas.reduce((a, b) => a + b, 0) / stepDeltas.length;
  const varStep =
    stepDeltas.reduce((a, d) => a + (d - meanStep) ** 2, 0) / Math.max(1, stepDeltas.length);
  const vol = Math.sqrt(varStep);

  // Calibration v1: require net movement for "volatile" when vol is moderate-only,
  // so oscillation around a flat mean is not labeled volatile from step noise alone.
  let labelOut = "stable";
  if (vol >= 6 && absD >= 3) labelOut = "volatile";
  else if (vol >= 5 && absD >= 2) labelOut = "volatile";
  else if (delta >= 4) labelOut = "rising";
  else if (delta <= -4) labelOut = "declining";
  else if (absD < 2) labelOut = "stable";
  else if (delta > 0) labelOut = "rising";
  else labelOut = "declining";

  const accel = stepDeltas.length >= 2 ? stepDeltas[stepDeltas.length - 1] - stepDeltas[0] : 0;
  let extra = "";
  if (vol < 3 && absD < 3) extra = "stabilizing";
  else if (accel > 2 && delta > 0) extra = "accelerating";
  else if (accel < -2 && delta < 0) extra = "accelerating";

  const combined =
    extra && (labelOut === "rising" || labelOut === "declining" || labelOut === "volatile")
      ? `${labelOut} · ${extra}`
      : labelOut;

  return {
    label: combined,
    detail: `${label}: ${s.map((x) => x.toFixed(1)).join(" → ")}`,
  };
}

/**
 * @param {{
 *   powerSeries: number[],
 *   conflictSeries: number[],
 *   influenceSeries: number[]
 * }} param0
 */
function computeFederalSnapshotTrends({ powerSeries, conflictSeries, influenceSeries }) {
  const p = classifySeries(powerSeries, "power/stability");
  const c = classifySeries(conflictSeries, "conflict temperature");
  const i = classifySeries(influenceSeries, "aggregate influence");

  return {
    power_trend_extended: p.label + " — " + p.detail,
    conflict_trend: c.label + " — " + c.detail,
    influence_trend: i.label + " — " + i.detail,
  };
}

/**
 * @param {{
 *   overallSeries: number[],
 *   powerSeries: number[],
 *   riskSeries: number[],
 *   opportunitySeries: number[]
 * }} param0
 */
function computeCountrySnapshotTrends({ overallSeries, powerSeries, riskSeries, opportunitySeries }) {
  const o = classifySeries(overallSeries, "overall");
  const p = classifySeries(powerSeries, "power_score");
  const r = classifySeries(riskSeries, "risk");
  const op = classifySeries(opportunitySeries, "opportunity");

  return {
    overall_trend_extended: o.label + " — " + o.detail,
    power_trend_extended: p.label + " — " + p.detail,
    risk_trend: r.label + " — " + r.detail,
    opportunity_trend: op.label + " — " + op.detail,
  };
}

module.exports = {
  classifySeries,
  computeFederalSnapshotTrends,
  computeCountrySnapshotTrends,
};
