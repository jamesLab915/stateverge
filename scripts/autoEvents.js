require("dotenv").config({ path: ".env.local", override: true });

const { createPgPool } = require("../lib/db/pgPool.js");
const { attachConsequencesAndActors } = require("../lib/eventAttachments.js");
const {
  prepareCountryEventSourceFields,
} = require("../lib/evolution/eventSourcePolicy.js");

console.log("OPENAI key loaded:", !!process.env.OPENAI_API_KEY);
console.log("USING KEY:", process.env.OPENAI_API_KEY?.slice(-6));

const pool = createPgPool();

async function generateEvent(country) {
  const prompt = `
Generate one short plausible economic or geopolitical event for country code "${country}".

Return STRICT JSON only:
{
  "event_type": "technology_policy",
  "title": "short title",
  "summary": "one short sentence",
  "impact_direction": "positive",
  "impact_strength": 5,
  "primary_actor": {
    "name": "short actor label",
    "actor_type": "government|party|firm|military|intl_org|central_bank|coalition|movement",
    "role": "initiated",
    "summary": "one line who they are"
  }
}

Rules:
- event_type must be one of: technology_policy, trade_policy, infrastructure, economy_policy
- impact_direction must be: positive or negative
- impact_strength must be an integer from 1 to 10
- primary_actor is optional; if unsure omit the whole primary_actor key
- role must be one of: initiated, influenced, opposed, benefited, escalated
- no markdown
- no explanation
`;

  const res = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: prompt }],
      temperature: 0.7,
    }),
  });

  const data = await res.json();

  if (!data.choices || !data.choices[0] || !data.choices[0].message) {
    console.log("Bad response:", data);
    return null;
  }

  const text = data.choices[0].message.content;

  try {
    return JSON.parse(text);
  } catch {
    console.log("Parse error:", text);
    return null;
  }
}

async function run() {
  const client = await pool.connect();
  try {
    const envList = process.env.EVOLUTION_COUNTRIES;
    const countries = envList
      ? envList
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
      : ["us", "cn", "jp"];

    console.log("autoEvents countries:", countries.join(", "));

    for (const c of countries) {
      const event = await generateEvent(c);
      if (!event) continue;

      const src = prepareCountryEventSourceFields({
        countryCode: c,
        sourceType: "ai_generated",
        confidenceScore: null,
      });

      const ins = await client.query(
        `
        INSERT INTO events (
          country_code,
          event_type,
          title,
          summary,
          event_date,
          impact_direction,
          impact_strength,
          source_name,
          source_url,
          source_type,
          confidence,
          applied_to_scores
        )
        VALUES ($1, $2, $3, $4, CURRENT_DATE, $5, $6, $7, $8, $9, $10, $11, FALSE)
        RETURNING id
        `,
        [
          c,
          event.event_type,
          event.title,
          event.summary,
          event.impact_direction,
          event.impact_strength,
          "OpenAI Auto",
          "https://api.openai.com",
          src.source_type,
          src.confidence,
        ]
      );

      const eventId = ins.rows[0].id;
      await attachConsequencesAndActors(client, eventId, c, event);

      console.log("Added event:", c, event.title, "id=", eventId);
    }
  } catch (error) {
    console.error("autoEvents failed:", error);
  } finally {
    client.release();
    await pool.end();
  }
}

run();
