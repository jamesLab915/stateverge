const { createPgPool } = require("../lib/db/pgPool.js");
const { attachConsequencesAndActors } = require("../lib/eventAttachments.js");
const {
  prepareCountryEventSourceFields,
} = require("../lib/evolution/eventSourcePolicy.js");

const pool = createPgPool();

async function addEvent() {
  const client = await pool.connect();
  try {
    const payload = {
      event_type: "technology_policy",
      title: "AI boom accelerates",
      summary:
        "Massive investment into AI sector boosts economy and innovation.",
      impact_direction: "positive",
      impact_strength: 5,
      primary_actor: {
        name: "National innovation board",
        actor_type: "coalition",
        role: "initiated",
        summary: "Coordinates industrial and research policy.",
      },
    };

    const src = prepareCountryEventSourceFields({
      countryCode: "us",
      sourceType: "institutional",
      confidenceScore: 88,
    });

    const result = await client.query(
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
      VALUES (
        'us',
        $1, $2, $3,
        CURRENT_DATE,
        $4, $5,
        'Internal Script',
        'https://example.com/ai-boom',
        $6,
        $7,
        FALSE
      )
      RETURNING id
      `,
      [
        payload.event_type,
        payload.title,
        payload.summary,
        payload.impact_direction,
        payload.impact_strength,
        src.source_type,
        src.confidence,
      ]
    );

    const eventId = result.rows[0].id;
    await attachConsequencesAndActors(client, eventId, "us", payload);

    console.log("Event added id=", eventId);
  } catch (error) {
    console.error("Failed to add event:", error);
  } finally {
    client.release();
    await pool.end();
  }
}

addEvent();
