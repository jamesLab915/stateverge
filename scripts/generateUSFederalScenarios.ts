/**
 * CLI: recompute rule context and refresh federal-scenario in federal_ai_cache (optional OpenAI).
 * Run from repo root: npx tsx scripts/generateUSFederalScenarios.ts
 */
import { config } from "dotenv";

config({ path: ".env.local" });
config();

import { pool } from "../lib/db";
import {
  getLatestFederalSnapshot,
  getLatestFederalEvents,
  getActorsByInfluence,
  getMostContestedActors,
  upsertFederalAiCache,
} from "../lib/db/usFederalQueries";
import { buildFederalScenarioRules } from "../lib/evolution/usFederalScenarioRules";
import { generateFederalScenarioAI } from "../lib/ai/usFederalInsight";

async function main() {
  const [snapshot, events, topInfl, topConflict] = await Promise.all([
    getLatestFederalSnapshot(),
    getLatestFederalEvents(20),
    getActorsByInfluence(8),
    getMostContestedActors(8),
  ]);

  const rules = buildFederalScenarioRules({
    snapshot: snapshot
      ? {
          overall_power_stability: snapshot.overall_power_stability as number | null,
          executive_cohesion: snapshot.executive_cohesion as number | null,
          cabinet_stability: snapshot.cabinet_stability as number | null,
          legislative_alignment: snapshot.legislative_alignment as number | null,
          conflict_temperature: snapshot.conflict_temperature as number | null,
          narrative_pressure: snapshot.narrative_pressure as number | null,
        }
      : null,
    recentEvents: events.map((e) => ({
      event_type: e.event_type,
      impact_direction: e.impact_direction,
      confidence: e.confidence,
    })),
    topByInfluence: topInfl.map((a) => ({
      name: a.name,
      slug: a.slug,
      conflict_index: a.conflict_index,
      political_influence_score: a.political_influence_score,
      formal_power_score: a.formal_power_score,
    })),
    topByConflict: topConflict.map((a) => ({
      name: a.name,
      slug: a.slug,
      conflict_index: a.conflict_index,
      political_influence_score: a.political_influence_score,
      formal_power_score: a.formal_power_score,
    })),
  });

  console.log(JSON.stringify(rules, null, 2));

  if (!process.env.OPENAI_API_KEY) {
    console.log("OPENAI_API_KEY not set; skipping federal_ai_cache upsert.");
    await pool.end();
    return;
  }

  const ai = await generateFederalScenarioAI(JSON.stringify(rules));
  await upsertFederalAiCache("federal-scenario", ai as Record<string, unknown>);
  console.log("federal-scenario cache updated.");
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
