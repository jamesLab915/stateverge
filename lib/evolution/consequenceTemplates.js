/**
 * Consequence templates: event_type → standardized impacts (truth layer, not AI).
 * Country + federal; country templates use stability_effect + risk_effect (+ impacts).
 */

const COUNTRY_DIMENSIONS = new Set([
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

function normType(t) {
  return String(t || "")
    .trim()
    .toLowerCase();
}

function scaleFromEvent(impactStrength, impactDirection) {
  const s = Number(impactStrength);
  const base = Number.isFinite(s) && s > 0 ? s / 5 : 0.6;
  const dir = impactDirection === "negative" ? -1 : impactDirection === "positive" ? 1 : 0;
  return { base, dir };
}

function mergeRowsByDimension(rows) {
  const map = new Map();
  for (const r of rows) {
    const k = r.dimension;
    if (!map.has(k)) {
      map.set(k, { ...r });
    } else {
      const o = map.get(k);
      o.impact_value = round2(Number(o.impact_value) + Number(r.impact_value));
    }
  }
  return [...map.values()];
}

/**
 * @param {{ impacts: { dimension: string, value: number, horizon: string }[], stability_effect: number, conflict_effect?: number, risk_effect?: number, explanation: string }} tmpl
 * @param {{ impact_strength?: number, impact_direction?: string }} event
 */
function scaleCountryTemplateFields(tmpl, event) {
  const { base, dir } = scaleFromEvent(event.impact_strength, event.impact_direction);
  // Neutral/unknown direction: dampen template magnitude to avoid false precision (calibration v1).
  const neutralDamp = dir === 0 ? 0.5 : 1;
  const mult = dir === 0 ? base * neutralDamp : base * dir;
  const riskSrc =
    tmpl.risk_effect !== undefined && tmpl.risk_effect !== null
      ? tmpl.risk_effect
      : tmpl.conflict_effect !== undefined
        ? tmpl.conflict_effect
        : 0;

  const impacts = tmpl.impacts.map((im) => ({
    dimension: im.dimension,
    value: round2(im.value * mult),
    horizon: im.horizon,
  }));

  const absScale = dir === 0 ? base * neutralDamp : Math.abs(mult);
  return {
    impacts,
    stability_effect: round2(tmpl.stability_effect * absScale),
    risk_effect: round2(riskSrc * absScale),
    explanation: tmpl.explanation,
  };
}

/** @param {any} tmpl federal template (uses conflict_effect) */
function scaleTemplateValues(tmpl, event) {
  const { base, dir } = scaleFromEvent(event.impact_strength, event.impact_direction);
  const neutralDamp = dir === 0 ? 0.5 : 1;
  const mult = dir === 0 ? base * neutralDamp : base * dir;
  const impacts = tmpl.impacts.map((im) => ({
    dimension: im.dimension,
    value: round2(im.value * mult),
    horizon: im.horizon,
  }));
  const absScale = dir === 0 ? base * neutralDamp : Math.abs(mult);
  return {
    impacts,
    stability_effect: round2(tmpl.stability_effect * absScale),
    conflict_effect: round2(tmpl.conflict_effect * absScale),
    explanation: tmpl.explanation,
  };
}

/** @type {Record<string, any>} */
const FEDERAL_TEMPLATES = {
  appointment: {
    impacts: [
      { dimension: "policy_control", value: 3, horizon: "short_term" },
      { dimension: "governance", value: 2, horizon: "short_term" },
      { dimension: "stability", value: 2, horizon: "immediate" },
    ],
    stability_effect: 1,
    conflict_effect: -1,
    explanation: "Staffing change: implementation capacity and agenda control shift (template).",
  },
  nomination: {
    impacts: [
      { dimension: "political_influence", value: 2, horizon: "short_term" },
      { dimension: "policy_control", value: 1, horizon: "medium_term" },
      { dimension: "alignment", value: 1, horizon: "short_term" },
    ],
    stability_effect: 0,
    conflict_effect: 1,
    explanation: "Nomination introduces confirmation uncertainty and coalition bargaining (template).",
  },
  confirmation: {
    impacts: [
      { dimension: "formal_power", value: 2, horizon: "immediate" },
      { dimension: "stability", value: 2, horizon: "short_term" },
      { dimension: "policy_control", value: 2, horizon: "short_term" },
    ],
    stability_effect: 2,
    conflict_effect: -2,
    explanation: "Senate confirmation locks formal authority and reduces vacancy risk (template).",
  },
  dismissal: {
    impacts: [
      { dimension: "stability", value: -3, horizon: "immediate" },
      { dimension: "conflict", value: 4, horizon: "immediate" },
      { dimension: "political_influence", value: -2, horizon: "short_term" },
    ],
    stability_effect: -3,
    conflict_effect: 4,
    explanation: "Removal disrupts leadership continuity and raises factional conflict (template).",
  },
  resignation: {
    impacts: [
      { dimension: "stability", value: -2, horizon: "immediate" },
      { dimension: "conflict", value: 2, horizon: "short_term" },
      { dimension: "alignment", value: -1, horizon: "short_term" },
    ],
    stability_effect: -2,
    conflict_effect: 2,
    explanation: "Voluntary exit — less blame signal than dismissal but still churn (template).",
  },
  executive_order: {
    impacts: [
      { dimension: "policy_control", value: 4, horizon: "immediate" },
      { dimension: "formal_power", value: 1, horizon: "short_term" },
    ],
    stability_effect: 0,
    conflict_effect: 1,
    explanation: "Unilateral directive shifts implementation; may trigger legal/political pushback (template).",
  },
  conflict: {
    impacts: [
      { dimension: "conflict", value: 5, horizon: "immediate" },
      { dimension: "stability", value: -2, horizon: "short_term" },
      { dimension: "political_influence", value: 2, horizon: "immediate" },
    ],
    stability_effect: -2,
    conflict_effect: 5,
    explanation: "Open friction across actors or branches (template).",
  },
  policy_shift: {
    impacts: [
      { dimension: "policy_control", value: 3, horizon: "medium_term" },
      { dimension: "alignment", value: 2, horizon: "medium_term" },
      { dimension: "department_power", value: 2, horizon: "medium_term" },
    ],
    stability_effect: 0,
    conflict_effect: 1,
    explanation: "Material change in policy stance reallocates agency priorities (template).",
  },
  investigation: {
    impacts: [
      { dimension: "conflict", value: 3, horizon: "short_term" },
      { dimension: "stability", value: -2, horizon: "medium_term" },
      { dimension: "political_influence", value: -2, horizon: "long_term" },
    ],
    stability_effect: -2,
    conflict_effect: 3,
    explanation: "Oversight / probe increases legal and reputational exposure (template).",
  },
  scandal: {
    impacts: [
      { dimension: "conflict", value: 4, horizon: "immediate" },
      { dimension: "stability", value: -3, horizon: "short_term" },
      { dimension: "political_influence", value: -3, horizon: "short_term" },
    ],
    stability_effect: -3,
    conflict_effect: 4,
    explanation: "Public credibility damage; defensive coalition behavior (template).",
  },
};

/**
 * Country: impacts + stability_effect + risk_effect (or legacy conflict_effect as risk proxy).
 * @type {Record<string, any>}
 */
const COUNTRY_TEMPLATES = {
  reform: {
    impacts: [
      { dimension: "governance", value: 3, horizon: "6-24m" },
      { dimension: "economy", value: 2, horizon: "6-24m" },
      { dimension: "innovation", value: 1, horizon: "12-36m" },
    ],
    stability_effect: 1,
    risk_effect: -1,
    explanation: "Institutional reform wave — rules and allocation shift (template).",
  },
  election: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "3-12m" },
      { dimension: "social_order", value: 1, horizon: "3-12m" },
      { dimension: "opportunity", value: 1, horizon: "6-18m" },
    ],
    stability_effect: 0,
    risk_effect: 2,
    explanation: "Electoral cycle — mandate and coalition uncertainty (template).",
  },
  conflict: {
    impacts: [
      { dimension: "social_order", value: -3, horizon: "immediate" },
      { dimension: "risk", value: 3, horizon: "3-12m" },
      { dimension: "economy", value: -1, horizon: "6-18m" },
    ],
    stability_effect: -3,
    risk_effect: 5,
    explanation: "Armed or severe political conflict channel (template).",
  },
  sanction: {
    impacts: [
      { dimension: "economy", value: -2, horizon: "3-12m" },
      { dimension: "openness", value: -3, horizon: "3-12m" },
      { dimension: "risk", value: 3, horizon: "immediate" },
    ],
    stability_effect: -1,
    risk_effect: 4,
    explanation: "External sanctions — trade and finance pressure (template).",
  },
  alliance_shift: {
    impacts: [
      { dimension: "openness", value: 2, horizon: "6-24m" },
      { dimension: "governance", value: 1, horizon: "6-18m" },
      { dimension: "risk", value: 1, horizon: "6-18m" },
    ],
    stability_effect: 0,
    risk_effect: 1,
    explanation: "Alliance / alignment realignment (template).",
  },
  industrial_policy: {
    impacts: [
      { dimension: "economy", value: 2, horizon: "6-24m" },
      { dimension: "innovation", value: 2, horizon: "6-24m" },
      { dimension: "infrastructure", value: 1, horizon: "12-36m" },
    ],
    stability_effect: 1,
    risk_effect: -1,
    explanation: "Sector targeting and industrial strategy (template).",
  },
  tech_breakthrough: {
    impacts: [
      { dimension: "innovation", value: 4, horizon: "6-18m" },
      { dimension: "future_potential", value: 3, horizon: "12-36m" },
      { dimension: "economy", value: 2, horizon: "6-18m" },
    ],
    stability_effect: 1,
    risk_effect: -1,
    explanation: "Major technology step-change (template).",
  },
  fiscal_crisis: {
    impacts: [
      { dimension: "economy", value: -3, horizon: "immediate" },
      { dimension: "risk", value: 4, horizon: "immediate" },
      { dimension: "governance", value: -2, horizon: "6-18m" },
    ],
    stability_effect: -2,
    risk_effect: 5,
    explanation: "Sovereign or fiscal stress (template).",
  },
  leadership_change: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "3-12m" },
      { dimension: "risk", value: 2, horizon: "3-12m" },
      { dimension: "opportunity", value: 1, horizon: "6-18m" },
    ],
    stability_effect: 0,
    risk_effect: 2,
    explanation: "Top leadership transition (template).",
  },
  social_unrest: {
    impacts: [
      { dimension: "social_order", value: -4, horizon: "immediate" },
      { dimension: "risk", value: 3, horizon: "immediate" },
      { dimension: "governance", value: -2, horizon: "3-12m" },
    ],
    stability_effect: -4,
    risk_effect: 4,
    explanation: "Mass protest / unrest channel (template).",
  },
  economic_response: {
    impacts: [
      { dimension: "economy", value: 2, horizon: "3-12m" },
      { dimension: "governance", value: 1, horizon: "3-12m" },
      { dimension: "risk", value: -1, horizon: "3-12m" },
    ],
    stability_effect: 0,
    risk_effect: -1,
    explanation: "Policy response to external economic shock (template).",
  },
  escalation: {
    impacts: [
      { dimension: "risk", value: 3, horizon: "immediate" },
      { dimension: "social_order", value: -2, horizon: "immediate" },
      { dimension: "opportunity", value: -1, horizon: "6-18m" },
    ],
    stability_effect: -2,
    risk_effect: 4,
    explanation: "Escalation spiral — intensity increases (template).",
  },
  economic_change: {
    impacts: [
      { dimension: "economy", value: 3, horizon: "6-24m" },
      { dimension: "human_capital", value: 1, horizon: "12-36m" },
      { dimension: "future_potential", value: 2, horizon: "12-36m" },
    ],
    stability_effect: 1,
    risk_effect: 0,
    explanation: "Structural economic shift (template; not always post-reform).",
  },
  appointment: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "6-18m" },
      { dimension: "opportunity", value: 2, horizon: "6-18m" },
    ],
    stability_effect: 1,
    risk_effect: -1,
    explanation: "Leadership appointment — capacity and direction (template).",
  },
  nomination: {
    impacts: [
      { dimension: "governance", value: 1, horizon: "3-12m" },
      { dimension: "risk", value: 1, horizon: "3-12m" },
    ],
    stability_effect: 0,
    risk_effect: 1,
    explanation: "Pending confirmation — uncertainty premium (template).",
  },
  confirmation: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "6-18m" },
      { dimension: "opportunity", value: 1, horizon: "6-18m" },
    ],
    stability_effect: 2,
    risk_effect: -1,
    explanation: "Confirmed authority — lower transition risk (template).",
  },
  dismissal: {
    impacts: [
      { dimension: "governance", value: -2, horizon: "immediate" },
      { dimension: "risk", value: 2, horizon: "6-18m" },
    ],
    stability_effect: -3,
    risk_effect: 4,
    explanation: "Forced removal — disruption and blame games (template).",
  },
  resignation: {
    impacts: [
      { dimension: "governance", value: -1, horizon: "immediate" },
      { dimension: "risk", value: 1, horizon: "3-12m" },
    ],
    stability_effect: -2,
    risk_effect: 2,
    explanation: "Voluntary exit — moderate disruption (template).",
  },
  executive_order: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "3-12m" },
      { dimension: "innovation", value: 1, horizon: "6-18m" },
    ],
    stability_effect: 0,
    risk_effect: 1,
    explanation: "Executive action — fast policy move (template).",
  },
  policy_shift: {
    impacts: [
      { dimension: "governance", value: 2, horizon: "6-24m" },
      { dimension: "economy", value: 1, horizon: "6-24m" },
    ],
    stability_effect: 0,
    risk_effect: 1,
    explanation: "Material policy reorientation (template).",
  },
  investigation: {
    impacts: [
      { dimension: "risk", value: 2, horizon: "6-18m" },
      { dimension: "governance", value: -1, horizon: "6-18m" },
    ],
    stability_effect: -2,
    risk_effect: 3,
    explanation: "Probe / oversight load (template).",
  },
  scandal: {
    impacts: [
      { dimension: "risk", value: 3, horizon: "immediate" },
      { dimension: "social_order", value: -2, horizon: "6-18m" },
    ],
    stability_effect: -3,
    risk_effect: 4,
    explanation: "Credibility shock (template).",
  },
};

function buildDefaultCountryConsequenceRows(event) {
  const dir =
    event.impact_direction === "negative" ? -1 : event.impact_direction === "positive" ? 1 : 0.6;
  const s = Number(event.impact_strength) || 3;
  const m = dir * (s / 5);
  return [
    {
      dimension: "governance",
      impact_value: round2(1.4 * m),
      time_horizon: "6-18m",
      confidence: 70,
      explanation: "Untyped event — default governance channel (template).",
    },
    {
      dimension: "risk",
      impact_value: round2(0.55 * Math.abs(m)),
      time_horizon: "3-12m",
      confidence: 70,
      explanation: "Untyped event — default risk channel (template).",
    },
    {
      dimension: "opportunity",
      impact_value: round2(0.35 * Math.max(0, m)),
      time_horizon: "6-18m",
      confidence: 70,
      explanation: "Untyped event — default opportunity channel (template).",
    },
  ];
}

/**
 * Country rows for event_consequences INSERT.
 * @param {{ country_code: string, event_type?: string, impact_direction?: string, impact_strength?: number }} event
 */
function buildCountryConsequenceRowsFromTemplate(event) {
  const code = event.country_code;
  if (!code) return [];
  const key = normType(event.event_type);
  const tmpl = COUNTRY_TEMPLATES[key];
  if (!tmpl) return [];

  const scaled = scaleCountryTemplateFields(tmpl, event);
  const rows = [];

  for (const im of scaled.impacts) {
    if (!COUNTRY_DIMENSIONS.has(im.dimension)) continue;
    const v = round2(im.value);
    if (Math.abs(v) < 0.001) continue;
    rows.push({
      dimension: im.dimension,
      impact_value: v,
      time_horizon: im.horizon,
      confidence: 72,
      explanation: scaled.explanation,
    });
  }

  if (Math.abs(scaled.stability_effect) >= 0.001) {
    rows.push({
      dimension: "social_order",
      impact_value: round2(scaled.stability_effect * 0.85),
      time_horizon: "immediate",
      explanation: `Stability channel (${scaled.explanation})`,
      confidence: 72,
    });
  }
  if (Math.abs(scaled.risk_effect) >= 0.001) {
    rows.push({
      dimension: "risk",
      impact_value: round2(scaled.risk_effect * 0.55),
      time_horizon: "3-12m",
      explanation: `Risk/stress channel (${scaled.explanation})`,
      confidence: 72,
    });
  }

  return mergeRowsByDimension(rows);
}

/**
 * Federal rows for federal_event_consequences INSERT (system-level targets).
 * @param {{ id: number, event_type?: string, department?: string|null, impact_strength?: number, impact_direction?: string, confidence?: string }} event
 */
function buildFederalConsequenceRowsFromTemplate(event) {
  const key = normType(event.event_type);
  const tmpl = FEDERAL_TEMPLATES[key];
  if (!tmpl) return [];
  const scaled = scaleTemplateValues(tmpl, event);
  const conf = event.confidence || "confirmed";
  const rows = [];

  for (const im of scaled.impacts) {
    const v = round2(im.value);
    if (Math.abs(v) < 0.001) continue;
    rows.push({
      target_type: "system",
      target_actor_id: null,
      target_department: event.department || null,
      dimension: im.dimension,
      impact_value: v,
      time_horizon: im.horizon,
      confidence: conf,
      explanation: scaled.explanation,
    });
  }

  if (Math.abs(scaled.stability_effect) >= 0.001) {
    rows.push({
      target_type: "system",
      target_actor_id: null,
      target_department: null,
      dimension: "stability",
      impact_value: round2(scaled.stability_effect),
      time_horizon: "short_term",
      confidence: conf,
      explanation: `Aggregate stability delta (${scaled.explanation})`,
    });
  }
  if (Math.abs(scaled.conflict_effect) >= 0.001) {
    rows.push({
      target_type: "system",
      target_actor_id: null,
      target_department: null,
      dimension: "conflict",
      impact_value: round2(scaled.conflict_effect),
      time_horizon: "immediate",
      confidence: conf,
      explanation: `Aggregate conflict stress (${scaled.explanation})`,
    });
  }

  return rows;
}

function getFederalTemplateDefinition(eventType) {
  return FEDERAL_TEMPLATES[normType(eventType)] || null;
}

function getCountryTemplateDefinition(eventType) {
  return COUNTRY_TEMPLATES[normType(eventType)] || null;
}

module.exports = {
  buildCountryConsequenceRowsFromTemplate,
  buildFederalConsequenceRowsFromTemplate,
  buildDefaultCountryConsequenceRows,
  mergeRowsByDimension,
  getFederalTemplateDefinition,
  getCountryTemplateDefinition,
  FEDERAL_TEMPLATES,
  COUNTRY_TEMPLATES,
  COUNTRY_DIMENSIONS,
};
