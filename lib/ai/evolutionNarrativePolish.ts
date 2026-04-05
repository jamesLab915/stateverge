import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export type EvolutionNarrativeRecord = {
  summary: string;
  direction: string;
  turning_points: { event: string; impact: string; why_important: string }[];
  key_drivers: string[];
  risks: string[];
};

function stripJson(text: string) {
  const t = text.trim();
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start >= 0 && end > start) return t.slice(start, end + 1);
  return t;
}

/**
 * Rewrites rule-engine narrative JSON for readability only; must not invent facts or scores.
 */
export async function polishEvolutionNarrativeJSON(
  ruleJson: EvolutionNarrativeRecord
): Promise<EvolutionNarrativeRecord> {
  const prompt = `You are an editor for a structured country evolution report.

RULE_JSON (from database + deterministic rules — source of truth):
${JSON.stringify(ruleJson).slice(0, 12000)}

Return STRICT JSON only, same keys and array shapes. Improve clarity and flow only.
Do not add new events, numbers, or claims not implied by RULE_JSON.
direction must remain one of: rising, declining, volatile, accelerating, stable (match RULE_JSON.direction when present).

{
  "summary": "2-4 sentences max",
  "direction": "rising|declining|volatile|accelerating|stable",
  "turning_points": [ { "event": "...", "impact": "...", "why_important": "..." } ],
  "key_drivers": ["...", "..."],
  "risks": ["...", "..."]
}`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.35,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as EvolutionNarrativeRecord;
  } catch {
    return ruleJson;
  }
}
