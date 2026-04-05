/**
 * Maps an event to structured consequence rows (dimension deltas).
 * Kept in sync with accumulateEventImpact in scripts/updateScores.js.
 */

const DIMENSIONS = new Set([
  "governance",
  "social_order",
  "economy",
  "human_capital",
  "infrastructure",
  "innovation",
  "openness",
  "future_potential",
  "risk",
  "opportunity",
  "power_score",
]);

function round2(x) {
  return Math.round(x * 100) / 100;
}

/**
 * @param {{ country_code: string, event_type?: string, impact_direction?: string, impact_strength?: number }} event
 * @returns {{ dimension: string, impact_value: number, time_horizon: string, confidence: number, explanation: string }[]}
 */
function buildConsequenceRows(event) {
  const code = event.country_code;
  if (!code) return [];

  const direction = event.impact_direction === "positive" ? 1 : -1;
  const s = Number(event.impact_strength) || 0;
  const t = String(event.event_type || "");
  const rows = [];

  const add = (dimension, raw, time_horizon, explanation) => {
    if (!DIMENSIONS.has(dimension)) return;
    const impact_value = round2(raw);
    if (Math.abs(impact_value) < 0.001) return;
    rows.push({
      dimension,
      impact_value,
      time_horizon,
      confidence: 70,
      explanation,
    });
  };

  if (t.includes("technology")) {
    add("innovation", direction * s, "6-18m", "Technology / R&D policy channel");
    add("governance", direction * s * 0.4, "6-18m", "Regulatory and state capacity follow-on");
  }
  if (t.includes("trade")) {
    add("economy", direction * s, "3-12m", "Trade and external demand");
    add("openness", direction * s * 0.7, "3-12m", "Cross-border integration");
  }
  if (t.includes("infrastructure")) {
    add("infrastructure", direction * s, "12-36m", "Physical / digital backbone");
    add("economy", direction * s * 0.25, "12-36m", "Productivity spillovers");
  }
  if (t.includes("economy")) {
    add("economy", direction * s, "3-12m", "Macro / industrial channel");
    add("governance", direction * s * 0.35, "3-12m", "Policy execution");
  }
  if (t.includes("policy") && !t.includes("economy")) {
    add("governance", direction * s * 0.6, "6-24m", "Policy and institutions");
  }

  if (direction === -1) {
    add("risk", Math.abs(s) * 0.55, "immediate", "Negative shock — risk accumulation");
    add("social_order", -Math.abs(s) * 0.35, "6-18m", "Cohesion / stability pressure");
  } else {
    add("opportunity", s * 0.55, "6-18m", "Upside optionality");
    add("future_potential", s * 0.2, "12-36m", "Long-horizon trajectory");
    add("human_capital", s * 0.15, "12-36m", "Skills and labor market");
  }

  return rows;
}

module.exports = { buildConsequenceRows, DIMENSIONS };
