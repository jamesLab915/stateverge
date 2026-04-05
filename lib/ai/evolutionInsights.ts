import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

export type ScenarioBundleAI = {
  bullish: { title: string; conditions: string[]; impact: string };
  neutral: { title: string; conditions: string[]; impact: string };
  bearish: { title: string; conditions: string[]; impact: string };
};

export async function generateScenarioBundleAI(
  countryName: string,
  ruleHint: string
): Promise<ScenarioBundleAI> {
  const prompt = `
Country focus: ${countryName}
Rule-engine hint (JSON): ${ruleHint}

Return STRICT JSON only, no markdown. Operator tone: concise, analytical, not academic.

{
  "bullish": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" },
  "neutral": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" },
  "bearish": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" }
}
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.45,
  });

  const text = response.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(text) as ScenarioBundleAI;
  } catch {
    return {
      bullish: { title: "Bullish", conditions: [], impact: text.slice(0, 400) },
      neutral: { title: "Neutral", conditions: [], impact: "" },
      bearish: { title: "Bearish", conditions: [], impact: "" },
    };
  }
}

export type CausalSummaryAI = {
  current_state_summary: string;
  major_causes: {
    event: string;
    dimension: string;
    impact: string;
    why_it_matters: string;
  }[];
  actor_summary: string[];
};

export async function generateCausalSummaryAI(
  countryName: string,
  factsJson: string
): Promise<CausalSummaryAI> {
  const prompt = `
Build a causal read for operators.

Country: ${countryName}
Facts (events + dimension impacts): ${factsJson}

Return STRICT JSON only, no markdown.

{
  "current_state_summary": "2 sentences max",
  "major_causes": [
    {
      "event": "short label",
      "dimension": "e.g. innovation",
      "impact": "what moved",
      "why_it_matters": "one line for overall/power"
    }
  ],
  "actor_summary": ["who is moving the board", "second line if any"]
}
Max 4 major_causes.
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.4,
  });

  const text = response.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(text) as CausalSummaryAI;
  } catch {
    return {
      current_state_summary: text.slice(0, 280),
      major_causes: [],
      actor_summary: [],
    };
  }
}

export type CompareCausalAI = {
  divergence_summary: string;
  left_driver_events: string[];
  right_driver_events: string[];
  structural_thesis: string;
};

export async function generateCompareCausalSummaryAI(
  leftName: string,
  rightName: string,
  factsJson: string
): Promise<CompareCausalAI> {
  const prompt = `
Compare causal drivers for ${leftName} vs ${rightName}.

Facts: ${factsJson}

Return STRICT JSON only, no markdown.

{
  "divergence_summary": "one sharp sentence on why their trajectories differ",
  "left_driver_events": ["...", "..."],
  "right_driver_events": ["...", "..."],
  "structural_thesis": "one sentence tying institutions, risk, and power"
}
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.45,
  });

  const text = response.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(text) as CompareCausalAI;
  } catch {
    return {
      divergence_summary: text.slice(0, 200),
      left_driver_events: [],
      right_driver_events: [],
      structural_thesis: "",
    };
  }
}

export type EvolutionSummaryAI = {
  headline: string;
  arc: string;
  inflections: string[];
  forward_watch: string;
};

export async function generateEvolutionSummaryAI(
  countryName: string,
  timelineHint: string
): Promise<EvolutionSummaryAI> {
  const prompt = `
Evolution arc for ${countryName}.
Timeline hint: ${timelineHint}

Return STRICT JSON only, no markdown.

{
  "headline": "one line",
  "arc": "2 sentences: past → present",
  "inflections": ["bullet 1", "bullet 2", "bullet 3"],
  "forward_watch": "one sentence what to monitor next"
}
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.5,
  });

  const text = response.choices[0]?.message?.content || "{}";
  try {
    return JSON.parse(text) as EvolutionSummaryAI;
  } catch {
    return {
      headline: "Evolution",
      arc: text.slice(0, 400),
      inflections: [],
      forward_watch: "",
    };
  }
}
