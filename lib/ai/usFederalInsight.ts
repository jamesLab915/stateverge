import OpenAI from "openai";
import type { FederalActorRow } from "@/lib/db/usFederalQueries";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export type FederalOverviewAI = {
  headline: string;
  stability: string;
  key_power_centers: string[];
  major_conflicts: string[];
  strategic_takeaway: string;
};

export type FederalActorInsightAI = {
  headline: string;
  current_role_summary: string;
  power_status: string;
  recent_actions: string[];
  conflict_exposure: string;
  strategic_takeaway: string;
};

export type FederalCausalAI = {
  current_state_summary: string;
  major_causes: {
    event: string;
    actors: string[];
    impact: string;
    confidence: string;
  }[];
  contested_points: string[];
};

/** AI layer only; rule JSON is passed in as facts. */
export type FederalScenarioAI = {
  headline: string;
  scenario_notes: {
    bullish: string;
    neutral: string;
    bearish: string;
  };
  watch_triggers: string[];
};

export type FederalActorCompareAI = {
  compare_headline: string;
  left_reading: string;
  right_reading: string;
  uncertainty_notes: string[];
};

function stripJson(text: string) {
  const t = text.trim();
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start >= 0 && end > start) return t.slice(start, end + 1);
  return t;
}

export async function generateFederalOverviewAI(
  factsJson: string
): Promise<FederalOverviewAI> {
  const prompt = `You are an analyst for a structured government power-tracking system (not gossip, not conspiracy).
Facts (from database only): ${factsJson.slice(0, 12000)}

Return STRICT JSON only, no markdown. Short, operator tone. Distinguish formal institutions from narrative.

{
  "headline": "one line",
  "stability": "one sentence on executive/institutional stability (hedge if data thin)",
  "key_power_centers": ["...", "..."],
  "major_conflicts": ["...", "..."],
  "strategic_takeaway": "one tight sentence"
}`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.35,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as FederalOverviewAI;
  } catch {
    return {
      headline: "Federal overview",
      stability: text.slice(0, 200),
      key_power_centers: [],
      major_conflicts: [],
      strategic_takeaway: "",
    };
  }
}

export async function generateFederalActorInsightAI(
  actor: FederalActorRow,
  contextJson: string
): Promise<FederalActorInsightAI> {
  const prompt = `Analyst brief for federal actor (structured system).
Actor: ${actor.name} (${actor.slug}) type ${actor.actor_type}
Scores from DB — formal ${actor.formal_power_score}, influence ${actor.political_influence_score}, conflict_index ${actor.conflict_index}, status ${actor.power_status}
Context: ${contextJson.slice(0, 8000)}

Return STRICT JSON only, no markdown. Do not invent court rulings or firings not in context. Mark uncertainty if context is thin.

{
  "headline": "one line",
  "current_role_summary": "one sentence",
  "power_status": "reuse or refine: Central|Rising|Stable|Contested|Fragile|Marginal style",
  "recent_actions": ["...", "..."],
  "conflict_exposure": "one sentence",
  "strategic_takeaway": "one sentence"
}`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.35,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as FederalActorInsightAI;
  } catch {
    return {
      headline: actor.name,
      current_role_summary: text.slice(0, 240),
      power_status: actor.power_status || "—",
      recent_actions: [],
      conflict_exposure: "",
      strategic_takeaway: "",
    };
  }
}

export async function generateFederalScenarioAI(
  ruleEngineJson: string
): Promise<FederalScenarioAI> {
  const prompt = `US federal power scenario layer. Below is STRICT JSON from a deterministic rules engine (not your invention). Do not change numeric confidences or invent facts.

RULE_JSON: ${ruleEngineJson.slice(0, 10000)}

Return STRICT JSON only, no markdown. Short operator phrases (max ~20 words per string field). Do not claim certainty beyond the rules.

{
  "headline": "one line — forward-looking but hedged",
  "scenario_notes": {
    "bullish": "one sentence on consolidation path",
    "neutral": "one sentence on contested equilibrium",
    "bearish": "one sentence on fragmentation/instability path"
  },
  "watch_triggers": ["metric or event type to watch", "..."]
}
Max 5 watch_triggers.`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as FederalScenarioAI;
  } catch {
    return {
      headline: "Federal scenarios",
      scenario_notes: {
        bullish: text.slice(0, 160),
        neutral: "",
        bearish: "",
      },
      watch_triggers: [],
    };
  }
}

export async function generateFederalActorCompareAI(
  ruleCompareJson: string,
  metricsJson: string
): Promise<FederalActorCompareAI> {
  const prompt = `Pairwise comparison of two US federal actors. RULE_JSON is deterministic (truth metrics in metricsJson).

RULE_JSON: ${ruleCompareJson.slice(0, 6000)}
METRICS_JSON: ${metricsJson.slice(0, 4000)}

Return STRICT JSON only, no markdown. Do not override formal_power numbers. Hedge if data thin.

{
  "compare_headline": "one line",
  "left_reading": "one sentence",
  "right_reading": "one sentence",
  "uncertainty_notes": ["...", "..."]
}
Max 4 uncertainty_notes.`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as FederalActorCompareAI;
  } catch {
    return {
      compare_headline: "Actor comparison",
      left_reading: text.slice(0, 200),
      right_reading: "",
      uncertainty_notes: [],
    };
  }
}

export async function generateFederalCausalAI(
  factsJson: string
): Promise<FederalCausalAI> {
  const prompt = `Causal overview for US federal power module. Facts from DB: ${factsJson.slice(0, 12000)}

Return STRICT JSON only. For each major_cause, confidence must be one of: confirmed, contested, speculative (based on source_type/confidence fields in facts).

{
  "current_state_summary": "2 sentences max",
  "major_causes": [
    { "event": "short", "actors": ["..."], "impact": "one line", "confidence": "confirmed|contested|speculative" }
  ],
  "contested_points": ["...", "..."]
}
Max 6 major_causes.`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as FederalCausalAI;
  } catch {
    return {
      current_state_summary: text.slice(0, 300),
      major_causes: [],
      contested_points: [],
    };
  }
}
