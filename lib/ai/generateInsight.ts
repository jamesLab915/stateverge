import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

type CountryLike = {
  name: string;
  region: string;
  capital?: string;
  population: number;
  gdp: number;
  overall?: number;
  governance?: number;
  social_order?: number;
  economy?: number;
  human_capital?: number;
  infrastructure?: number;
  innovation?: number;
  openness?: number;
  future_potential?: number;
  risk?: number;
  opportunity?: number;
  power_score?: number | null;
  power_classification?: string | null;
  score?: Partial<CountryLike>;
};

function pickStr(c: CountryLike, key: keyof CountryLike): string {
  const v = c[key] ?? c.score?.[key];
  return v == null || v === "" ? "—" : String(v);
}

function scoreBlock(label: string, c: CountryLike) {
  return `
${label}:
- Name: ${c.name}
- Region: ${c.region}
- GDP: ${c.gdp}
- Population: ${c.population}
- Overall Score: ${pickStr(c, "overall")}
- Power Score: ${pickStr(c, "power_score")}
- Power Class: ${pickStr(c, "power_classification")}
- Risk: ${pickStr(c, "risk")}
- Opportunity: ${pickStr(c, "opportunity")}
- Governance: ${pickStr(c, "governance")}
- Social Order: ${pickStr(c, "social_order")}
- Economy: ${pickStr(c, "economy")}
- Human Capital: ${pickStr(c, "human_capital")}
- Infrastructure: ${pickStr(c, "infrastructure")}
- Innovation: ${pickStr(c, "innovation")}
- Openness: ${pickStr(c, "openness")}
- Future Potential: ${pickStr(c, "future_potential")}
`.trim();
}

export async function generateAIInsight(left: CountryLike, right: CountryLike) {
  const L: CountryLike = { ...left, ...left.score };
  const R: CountryLike = { ...right, ...right.score };

  const prompt = `
Compare ${L.name} and ${R.name} for operators and decision-makers.

${scoreBlock("Country A", L as CountryLike & Record<string, unknown>)}

${scoreBlock("Country B", R as CountryLike & Record<string, unknown>)}

Return STRICT JSON only, no markdown, no explanation. Tone: sharp, concise, analytical, operator-focused, not academic.

{
  "winner": "country name that leads on overall structural advantage",
  "power_delta_summary": "one sentence on who leads on power_score and by what kind of margin (approximate)",
  "core_advantages": ["advantage 1", "advantage 2"],
  "core_risks": ["risk 1", "risk 2"],
  "bottom_line": "one decisive sentence for deploy capital / enter market / hedge / wait"
}
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.55,
  });

  const text = response.choices[0]?.message?.content || "{}";

  try {
    return JSON.parse(text) as {
      winner: string;
      power_delta_summary: string;
      core_advantages: string[];
      core_risks: string[];
      bottom_line: string;
    };
  } catch {
    return {
      winner: "Unknown",
      power_delta_summary: text.slice(0, 200),
      core_advantages: [],
      core_risks: [],
      bottom_line: "No structured output generated.",
    };
  }
}

export async function generateCountryInsight(country: CountryLike) {
  const c: CountryLike = { ...country, ...country.score };

  const prompt = `
Evolution / operator read for this country (structure + power + risk).

Country:
- Name: ${c.name}
- Region: ${c.region}
- Capital: ${c.capital}
- Population: ${c.population}
- GDP: ${c.gdp}
- Overall Score: ${c.overall}
- Power Score: ${c.power_score}
- Power Classification: ${c.power_classification}
- Governance: ${c.governance}
- Social Order: ${c.social_order}
- Economy: ${c.economy}
- Human Capital: ${c.human_capital}
- Infrastructure: ${c.infrastructure}
- Innovation: ${c.innovation}
- Openness: ${c.openness}
- Future Potential: ${c.future_potential}
- Risk: ${c.risk}
- Opportunity: ${c.opportunity}

Return STRICT JSON only, no markdown. Concise, analytical, operator-focused, not academic.

{
  "headline": "one-line headline",
  "trend": "rising | stable | declining",
  "drivers": ["driver 1", "driver 2", "driver 3"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "strategic_takeaway": "2 tight sentences"
}

Use trend as exactly one of: rising, stable, declining.
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.65,
  });

  const text = response.choices[0]?.message?.content || "{}";

  try {
    return JSON.parse(text) as {
      headline: string;
      trend: string;
      drivers: string[];
      weaknesses: string[];
      strategic_takeaway: string;
    };
  } catch {
    return {
      headline: "No headline generated.",
      trend: "stable",
      drivers: [],
      weaknesses: [],
      strategic_takeaway: text || "No insight generated.",
    };
  }
}

export async function generateCountryPowerInsight(
  country: CountryLike,
  context: {
    power_trend: string;
    power_delta: number | null;
    power_drivers_hint: string[];
    event_hints: string[];
  }
) {
  const c: CountryLike = { ...country, ...country.score };

  const prompt = `
Power-focused brief for operators.

Country: ${c.name} (${c.region})
Power score: ${c.power_score}
Power class: ${c.power_classification}
Trend label: ${context.power_trend}
Power delta vs prior snapshot: ${context.power_delta ?? "n/a"}
Suggested structural drivers (hints, you may refine): ${JSON.stringify(context.power_drivers_hint)}
Recent event hints: ${JSON.stringify(context.event_hints)}
Dimensions snapshot — economy ${c.economy}, innovation ${c.innovation}, infrastructure ${c.infrastructure}, governance ${c.governance}, openness ${c.openness}, future_potential ${c.future_potential}, risk ${c.risk}

Return STRICT JSON only, no markdown. Sharp, concise, analytical, operator-focused.

{
  "headline": "one punchy line",
  "trend": "rising | stable | declining",
  "drivers": ["driver 1", "driver 2", "driver 3"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "strategic_takeaway": "one tight paragraph"
}

Use trend as exactly one of: rising, stable, declining. Prefer aligning with: ${context.power_trend}.
`;

  const response = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.55,
  });

  const text = response.choices[0]?.message?.content || "{}";

  try {
    return JSON.parse(text) as {
      headline: string;
      trend: string;
      drivers: string[];
      weaknesses: string[];
      strategic_takeaway: string;
    };
  } catch {
    return {
      headline: "Power view unavailable.",
      trend: context.power_trend,
      drivers: context.power_drivers_hint.slice(0, 3),
      weaknesses: [],
      strategic_takeaway: text.slice(0, 280) || "No insight generated.",
    };
  }
}
