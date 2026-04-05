import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export type EvolutionContrastRecord = {
  summary: string;
  direction_gap: string;
  divergence_points: { event: string; impact: string; why_it_mattered: string }[];
  left_drivers: string[];
  right_drivers: string[];
  stability_comparison: string;
  bottom_line: string;
};

function stripJson(text: string) {
  const t = text.trim();
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start >= 0 && end > start) return t.slice(start, end + 1);
  return t;
}

/**
 * Clarifies wording only; caller merges back direction_gap / stability_comparison / bottom_line from rules if needed.
 */
export async function polishEvolutionContrastJSON(
  ruleJson: EvolutionContrastRecord
): Promise<EvolutionContrastRecord> {
  const prompt = `Editor for a two-country evolution contrast (database-backed rules).

RULE_JSON (source of truth for facts):
${JSON.stringify(ruleJson).slice(0, 12000)}

Return STRICT JSON only, same keys and array shapes. Improve clarity; do not invent events or numbers.

{
  "summary": "3-5 sentences max",
  "direction_gap": "keep very close to rule text",
  "divergence_points": [ { "event": "...", "impact": "...", "why_it_mattered": "..." } ],
  "left_drivers": ["...", "..."],
  "right_drivers": ["...", "..."],
  "stability_comparison": "keep close to rule",
  "bottom_line": "one tight paragraph"
}`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.35,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(stripJson(text)) as EvolutionContrastRecord;
  } catch {
    return ruleJson;
  }
}
