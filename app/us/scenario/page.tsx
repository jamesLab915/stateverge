import {
  getLatestFederalSnapshot,
  getLatestFederalEvents,
  getActorsByInfluence,
  getMostContestedActors,
  getFederalAiCache,
  upsertFederalAiCache,
} from "@/lib/db/usFederalQueries";
import { buildFederalScenarioRules } from "@/lib/evolution/usFederalScenarioRules";
import { generateFederalScenarioAI } from "@/lib/ai/usFederalInsight";

export const dynamic = "force-dynamic";

export default async function USFederalScenarioPage() {
  let loadError: string | null = null;
  let snapshot: Awaited<ReturnType<typeof getLatestFederalSnapshot>> = null;
  let events: Awaited<ReturnType<typeof getLatestFederalEvents>> = [];
  let topInfl: Awaited<ReturnType<typeof getActorsByInfluence>> = [];
  let topConflict: Awaited<ReturnType<typeof getMostContestedActors>> = [];

  try {
    [snapshot, events, topInfl, topConflict] = await Promise.all([
      getLatestFederalSnapshot(),
      getLatestFederalEvents(20),
      getActorsByInfluence(8),
      getMostContestedActors(8),
    ]);
  } catch (e) {
    loadError = e instanceof Error ? e.message : "Database error";
  }

  if (loadError) {
    return (
      <main className="px-6 py-12">
        <p className="text-red-600">{loadError}</p>
      </main>
    );
  }

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

  let ai = (await getFederalAiCache("federal-scenario")) as {
    headline?: string;
    scenario_notes?: { bullish?: string; neutral?: string; bearish?: string };
    watch_triggers?: string[];
  } | null;

  if (!ai && process.env.OPENAI_API_KEY) {
    try {
      ai = await generateFederalScenarioAI(JSON.stringify(rules));
      await upsertFederalAiCache("federal-scenario", ai as Record<string, unknown>);
    } catch {
      ai = null;
    }
  }

  const cards: { key: "bullish" | "neutral" | "bearish"; label: string; tone: string }[] = [
    { key: "bullish", label: "Bullish / consolidation", tone: "border-emerald-200 bg-emerald-50/50" },
    { key: "neutral", label: "Neutral / contested equilibrium", tone: "border-amber-200 bg-amber-50/40" },
    { key: "bearish", label: "Bearish / fragmentation", tone: "border-rose-200 bg-rose-50/40" },
  ];

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-6xl">
        <h1 className="text-3xl font-bold">US federal power scenarios</h1>
        <p className="mt-2 max-w-3xl text-sm text-gray-600">
          Rule engine reads PostgreSQL snapshots, events, and actor indices. Three fixed branches;
          confidences are model scores, not forecasts of probability. AI text is cached in{" "}
          <code className="text-xs">federal_ai_cache</code> under <code className="text-xs">federal-scenario</code>.
        </p>

        <section className="mt-10 rounded-2xl border border-slate-200 bg-slate-50 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-600">
            Current composite judgment
          </h2>
          <p className="mt-3 text-lg text-gray-900">{rules.overall_judgment}</p>
          <p className="mt-2 text-sm text-gray-600">
            Internal consolidation index (rules):{" "}
            <span className="font-mono font-semibold">{rules.consolidation_index}</span> · 0 = fragmentation
            stress, 100 = consolidation tilt in this model.
          </p>
        </section>

        {ai && (
          <section className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-6">
            <h2 className="text-lg font-semibold text-indigo-950">AI scenario summary (cached)</h2>
            <p className="mt-2 text-xl font-bold">{ai.headline}</p>
            <div className="mt-4 grid gap-4 text-sm md:grid-cols-3">
              <div>
                <p className="text-xs font-medium text-gray-500">Bullish note</p>
                <p className="mt-1 text-gray-800">{ai.scenario_notes?.bullish}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500">Neutral note</p>
                <p className="mt-1 text-gray-800">{ai.scenario_notes?.neutral}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500">Bearish note</p>
                <p className="mt-1 text-gray-800">{ai.scenario_notes?.bearish}</p>
              </div>
            </div>
            <div className="mt-6">
              <p className="text-xs font-semibold uppercase text-gray-500">Watch triggers</p>
              <ul className="mt-2 list-disc pl-5 text-sm text-gray-800">
                {(ai.watch_triggers || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          </section>
        )}

        <div className="mt-12 grid gap-8 lg:grid-cols-3">
          {cards.map(({ key, label, tone }) => {
            const branch = rules[key];
            return (
              <section
                key={key}
                className={`flex flex-col rounded-2xl border p-6 shadow-sm ${tone}`}
              >
                <h2 className="text-lg font-semibold text-gray-900">{label}</h2>
                <p className="mt-1 text-sm font-medium text-gray-800">{branch.title}</p>
                <p className="mt-4 text-3xl font-bold text-gray-900">{branch.confidence}</p>
                <p className="text-xs text-gray-600">Scenario confidence (model)</p>
                <div className="mt-6 flex-1">
                  <p className="text-xs font-semibold uppercase text-gray-600">Conditions</p>
                  <ul className="mt-2 list-disc pl-5 text-sm text-gray-800">
                    {branch.conditions.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
                <div className="mt-6">
                  <p className="text-xs font-semibold uppercase text-gray-600">Key actors</p>
                  <p className="mt-1 text-sm text-gray-800">{branch.key_actors.join(" · ")}</p>
                </div>
                <div className="mt-4 border-t border-black/5 pt-4">
                  <p className="text-xs font-semibold uppercase text-gray-600">Possible impact</p>
                  <p className="mt-1 text-sm text-gray-800">{branch.impact}</p>
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </main>
  );
}
