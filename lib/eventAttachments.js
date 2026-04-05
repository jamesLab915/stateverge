const { buildConsequenceRows } = require("./eventConsequenceMapping.js");

/**
 * @param {import("pg").PoolClient} client
 * @param {number} eventId
 * @param {string} countryCode
 * @param {{ event_type?: string, impact_direction?: string, impact_strength?: number, primary_actor?: { name?: string, actor_type?: string, role?: string, summary?: string } }} eventPayload
 */
async function attachConsequencesAndActors(client, eventId, countryCode, eventPayload) {
  const rows = buildConsequenceRows({
    country_code: countryCode,
    event_type: eventPayload.event_type,
    impact_direction: eventPayload.impact_direction,
    impact_strength: eventPayload.impact_strength,
  });

  for (const r of rows) {
    try {
      await client.query(
        `
        INSERT INTO event_consequences (
          event_id, target_country_code, dimension, impact_value,
          time_horizon, confidence, explanation
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        `,
        [
          eventId,
          countryCode,
          r.dimension,
          r.impact_value,
          r.time_horizon,
          r.confidence,
          r.explanation,
        ]
      );
    } catch (e) {
      console.warn("event_consequences insert skipped:", e.message);
    }
  }

  const pa = eventPayload.primary_actor;
  if (pa && pa.name) {
    await attachPrimaryActorIfPresent(client, eventId, countryCode, pa);
  }
}

/**
 * Inserts actors + event_actors when primary_actor is present (enrichment / ingest).
 * @param {import("pg").PoolClient} client
 * @param {number} eventId
 * @param {string} countryCode
 * @param {{ name?: string, actor_type?: string, role?: string, summary?: string }} primaryActor
 */
async function attachPrimaryActorIfPresent(client, eventId, countryCode, primaryActor) {
  const pa = primaryActor;
  if (!pa || !pa.name) return;
  try {
    const ar = await client.query(
      `
      INSERT INTO actors (name, actor_type, country_code, summary)
      VALUES ($1, $2, $3, $4)
      RETURNING id
      `,
      [
        String(pa.name).slice(0, 200),
        String(pa.actor_type || "coalition").slice(0, 80),
        countryCode,
        pa.summary ? String(pa.summary).slice(0, 500) : null,
      ]
    );
    const actorId = ar.rows[0].id;
    const role = String(pa.role || "influenced").slice(0, 40);
    await client.query(
      `
      INSERT INTO event_actors (event_id, actor_id, role)
      VALUES ($1, $2, $3)
      ON CONFLICT (event_id, actor_id, role) DO NOTHING
      `,
      [eventId, actorId, role]
    );
  } catch (e) {
    console.warn("actor / event_actors insert skipped:", e.message);
  }
}

module.exports = { attachConsequencesAndActors, attachPrimaryActorIfPresent };
