/**
 * CLI: refresh actor-compare cache for two slugs (optional OpenAI).
 * Usage: npx tsx scripts/generateUSFederalActorCompare.ts <leftSlug> <rightSlug>
 */
import { config } from "dotenv";

config({ path: ".env.local" });
config();

import { pool } from "../lib/db";
import {
  getActorBySlug,
  countActorEventsSince,
  upsertFederalAiCache,
} from "../lib/db/usFederalQueries";
import {
  buildFederalActorCompareRules,
  federalActorCompareCacheKey,
} from "../lib/evolution/usFederalActorCompareRules";
import { generateFederalActorCompareAI } from "../lib/ai/usFederalInsight";

function sinceDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

async function main() {
  const left = process.argv[2];
  const right = process.argv[3];
  if (!left || !right) {
    console.error("Usage: npx tsx scripts/generateUSFederalActorCompare.ts <leftSlug> <rightSlug>");
    process.exit(1);
  }

  const since = sinceDaysAgo(90);
  const [actorL, actorR] = await Promise.all([getActorBySlug(left), getActorBySlug(right)]);
  if (!actorL || !actorR) {
    console.error("One or both actors not found.");
    process.exit(1);
  }

  const [cntL, cntR] = await Promise.all([
    countActorEventsSince(actorL.id, since),
    countActorEventsSince(actorR.id, since),
  ]);

  const rules = buildFederalActorCompareRules({
    left: actorL,
    right: actorR,
    leftRecentEventCount: cntL,
    rightRecentEventCount: cntR,
  });

  const cacheKey = federalActorCompareCacheKey(left, right);
  console.log(JSON.stringify({ cacheKey, rules }, null, 2));

  if (!process.env.OPENAI_API_KEY) {
    console.log("OPENAI_API_KEY not set; skipping federal_ai_cache upsert.");
    await pool.end();
    return;
  }

  const metricsJson = JSON.stringify({
    left: {
      slug: actorL.slug,
      name: actorL.name,
      formal_power_score: actorL.formal_power_score,
      political_influence_score: actorL.political_influence_score,
      conflict_index: actorL.conflict_index,
      x_signal_score: actorL.x_signal_score,
      agenda_alignment_score: actorL.agenda_alignment_score,
      power_status: actorL.power_status,
    },
    right: {
      slug: actorR.slug,
      name: actorR.name,
      formal_power_score: actorR.formal_power_score,
      political_influence_score: actorR.political_influence_score,
      conflict_index: actorR.conflict_index,
      x_signal_score: actorR.x_signal_score,
      agenda_alignment_score: actorR.agenda_alignment_score,
      power_status: actorR.power_status,
    },
    window_days: 90,
    since,
    event_counts: { left: cntL, right: cntR },
  });

  const ai = await generateFederalActorCompareAI(JSON.stringify(rules), metricsJson);
  await upsertFederalAiCache(cacheKey, ai as Record<string, unknown>);
  console.log(`${cacheKey} cache updated.`);
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
