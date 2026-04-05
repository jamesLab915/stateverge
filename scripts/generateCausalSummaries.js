require("dotenv").config({ path: ".env.local", override: true });

const { createPgPool } = require("../lib/db/pgPool.js");
const OpenAI = require("openai");

const pool = createPgPool();

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

async function loadEvents(clientPg, code, limit) {
  const { rows: events } = await clientPg.query(
    `
    SELECT id, title, summary, event_type, event_date, impact_direction, impact_strength
    FROM events WHERE country_code = $1 ORDER BY id DESC LIMIT $2
    `,
    [code, limit]
  );
  const out = [];
  for (const e of events) {
    let cons;
    try {
      cons = await clientPg.query(
        `SELECT dimension, impact_value, time_horizon, explanation FROM event_consequences WHERE event_id = $1`,
        [e.id]
      );
    } catch {
      cons = { rows: [] };
    }
    out.push({
      title: e.title,
      summary: e.summary,
      impact_direction: e.impact_direction,
      impact_strength: e.impact_strength,
      consequences: cons.rows,
    });
  }
  return out;
}

async function main() {
  if (!process.env.OPENAI_API_KEY) {
    console.log("No OPENAI_API_KEY; skip generateCausalSummaries.");
    await pool.end();
    return;
  }

  const { rows: countries } = await pool.query(
    `SELECT c.code, c.name, s.overall, s.power_score FROM countries c JOIN country_scores s ON s.country_code = c.code`
  );

  const pg = await pool.connect();
  try {
    for (const c of countries) {
      const ev = await loadEvents(pg, c.code, 10);
      const facts = JSON.stringify(ev).slice(0, 12000);
      const prompt = `Build a causal read for operators.

Country: ${c.name} (${c.code})
Facts (events + dimension impacts): ${facts}

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
Max 4 major_causes.`;

      const res = await client.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: prompt }],
        temperature: 0.35,
      });

      const text = res.choices[0]?.message?.content || "{}";
      let payload;
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { current_state_summary: text.slice(0, 400), major_causes: [], actor_summary: [] };
      }

      await pg.query(
        `
        INSERT INTO ai_insights_cache (cache_key, payload, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (cache_key) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
        `,
        [`causal:${c.code}`, JSON.stringify(payload)]
      );
      console.log("Cached causal:", c.code);
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
