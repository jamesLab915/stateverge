require("dotenv").config({ path: ".env.local", override: true });

const { createPgPool } = require("../lib/db/pgPool.js");
const OpenAI = require("openai");

const pool = createPgPool();

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

async function main() {
  if (!process.env.OPENAI_API_KEY) {
    console.log("No OPENAI_API_KEY; skip generateScenarios.");
    await pool.end();
    return;
  }

  const { rows: countries } = await pool.query(
    `
    SELECT c.code, c.name, s.*,
      (SELECT power_trend FROM country_score_snapshots z
       WHERE z.country_code = c.code ORDER BY z.id DESC LIMIT 1) AS power_trend
    FROM countries c
    JOIN country_scores s ON s.country_code = c.code
    `
  );

  const pg = await pool.connect();
  try {
    for (const c of countries) {
      const { rows: ev } = await pg.query(
        `SELECT title, impact_direction FROM events WHERE country_code = $1 ORDER BY id DESC LIMIT 6`,
        [c.code]
      );

      const ruleHint = JSON.stringify({
        risk: c.risk,
        opportunity: c.opportunity,
        future_potential: c.future_potential,
        power_trend: c.power_trend,
        recent_events: ev,
      }).slice(0, 8000);

      const prompt = `Country focus: ${c.name} (${c.code})
Rule-engine hint: ${ruleHint}

Return STRICT JSON only, no markdown. Operator tone.

{
  "bullish": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" },
  "neutral": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" },
  "bearish": { "title": "...", "conditions": ["...", "..."], "impact": "one paragraph" }
}`;

      const res = await client.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: prompt }],
        temperature: 0.4,
      });

      const text = res.choices[0]?.message?.content || "{}";
      let payload;
      try {
        payload = JSON.parse(text);
      } catch {
        payload = {
          bullish: { title: "Bullish", conditions: [], impact: text.slice(0, 300) },
          neutral: { title: "Neutral", conditions: [], impact: "" },
          bearish: { title: "Bearish", conditions: [], impact: "" },
        };
      }

      await pg.query(
        `
        INSERT INTO ai_insights_cache (cache_key, payload, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (cache_key) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
        `,
        [`scenario:${c.code}`, JSON.stringify(payload)]
      );
      console.log("Cached scenario:", c.code);
    }
  } finally {
    pg.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
