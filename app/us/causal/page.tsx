import {
  getCausalOverviewData,
  getCausalChainsOverview,
  getFederalAiCache,
  upsertFederalAiCache,
} from "@/lib/db/usFederalQueries";
import { generateFederalCausalAI } from "@/lib/ai/usFederalInsight";

export const dynamic = "force-dynamic";

export default async function USFederalCausalPage() {
  let data: Awaited<ReturnType<typeof getCausalOverviewData>> = [];
  let chains: Awaited<ReturnType<typeof getCausalChainsOverview>> = [];
  let err: string | null = null;
  try {
    [data, chains] = await Promise.all([getCausalOverviewData(), getCausalChainsOverview(6)]);
  } catch (e) {
    err = e instanceof Error ? e.message : "Error";
  }

  if (err) {
    return (
      <main className="px-6 py-12">
        <p className="text-red-600">{err}</p>
      </main>
    );
  }

  let causal = (await getFederalAiCache("federal-causal")) as {
    current_state_summary?: string;
    major_causes?: {
      event: string;
      actors: string[];
      impact: string;
      confidence: string;
    }[];
    contested_points?: string[];
  } | null;

  if (!causal && process.env.OPENAI_API_KEY) {
    const facts = JSON.stringify(
      data.map((d) => ({
        event: d.event,
        actors: d.actors,
        consequences: d.consequences,
      }))
    ).slice(0, 12000);
    try {
      causal = await generateFederalCausalAI(facts);
      await upsertFederalAiCache("federal-causal", causal as Record<string, unknown>);
    } catch {
      causal = null;
    }
  }

  return (
    <main className="min-h-screen bg-white px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-3xl font-bold">Federal causal overview</h1>
        <p className="mt-2 text-sm text-gray-600">
          Truth layer: events, roles, and consequence rows with confidence. AI layer below is
          cached only and labels claims as confirmed / contested / speculative.
        </p>

        {chains.length > 0 && (
          <section className="mt-10">
            <h2 className="text-lg font-semibold">Linked event chains (parent_event_id)</h2>
            <p className="mt-1 text-sm text-gray-600">
              Rule-linked sequences: dismissal→appointment, scandal→investigation, etc.
            </p>
            <div className="mt-4 space-y-8">
              {chains.map((chain, ci) => (
                <div
                  key={ci}
                  className="rounded-2xl border border-gray-200 bg-white p-4 text-sm"
                >
                  <p className="text-xs font-semibold uppercase text-gray-500">
                    Chain {ci + 1} · {chain.length} step(s)
                  </p>
                  <ol className="mt-3 list-decimal space-y-4 pl-5">
                    {chain.map((step, si) => {
                      const ev = step.event as {
                        id: number;
                        title: string;
                        event_type: string;
                        event_date: string;
                      };
                      return (
                        <li key={ev.id ?? si}>
                          <span className="font-medium">
                            {ev.event_date} · {ev.event_type}
                          </span>
                          <span className="text-gray-800"> — {ev.title}</span>
                          {step.consequences.length > 0 && (
                            <p className="mt-1 text-xs text-gray-600">
                              {(step.consequences as { dimension: string; impact_value: number }[])
                                .map((c) => `${c.dimension}: ${c.impact_value}`)
                                .join(" · ")}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ))}
            </div>
          </section>
        )}

        {causal && (
          <section className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/50 p-6">
            <h2 className="text-lg font-semibold">AI causal summary (federal_ai_cache)</h2>
            <p className="mt-3 text-sm text-gray-800">{causal.current_state_summary}</p>
            <ul className="mt-6 space-y-4 text-sm">
              {(causal.major_causes || []).map((m, i) => (
                <li key={i} className="rounded-lg border border-white/80 bg-white/80 p-4">
                  <p className="font-semibold">{m.event}</p>
                  <p className="text-gray-600">{m.actors?.join(" · ")}</p>
                  <p className="mt-2">{m.impact}</p>
                  <p className="mt-2 text-xs font-medium uppercase text-gray-500">
                    {m.confidence}
                  </p>
                </li>
              ))}
            </ul>
            <div className="mt-6">
              <p className="font-medium text-amber-900">Contested / speculative points</p>
              <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
                {(causal.contested_points || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          </section>
        )}

        <section className="mt-12">
          <h2 className="text-lg font-semibold">Event chain (database)</h2>
          <div className="mt-4 space-y-6">
            {data.map(({ event, actors, consequences }) => (
              <div key={event.id} className="rounded-2xl border border-gray-200 p-5 text-sm">
                <div className="flex flex-wrap justify-between gap-2">
                  <h3 className="font-semibold">{event.title}</h3>
                  <span className="text-xs text-gray-500">
                    {event.event_date} · {event.confidence} · {event.source_type}
                  </span>
                </div>
                <p className="mt-2 text-gray-600">{event.summary}</p>
                <p className="mt-3 text-xs font-medium text-gray-700">Actors &amp; roles</p>
                <ul className="mt-1 list-disc pl-5">
                  {actors.map((a: { name: string; role: string; stance: string | null }) => (
                    <li key={a.name + a.role}>
                      {a.name}: {a.role}
                      {a.stance ? ` (${a.stance})` : ""}
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-xs font-medium text-gray-700">Consequences</p>
                <ul className="mt-1 list-disc pl-5 text-gray-700">
                  {consequences.length === 0 ? (
                    <li className="text-gray-400">None</li>
                  ) : (
                    consequences.map(
                      (c: {
                        dimension: string;
                        impact_value: number;
                        confidence: string;
                        explanation: string | null;
                        target_department: string | null;
                        target_actor_name: string | null;
                      }) => (
                        <li key={c.dimension + String(c.impact_value) + (c.explanation || "")}>
                          {c.dimension} {c.impact_value > 0 ? "+" : ""}
                          {c.impact_value} · {c.confidence}
                          {c.target_actor_name ? ` → ${c.target_actor_name}` : ""}
                          {c.target_department ? ` @ ${c.target_department}` : ""} —{" "}
                          {c.explanation || "—"}
                        </li>
                      )
                    )
                  )}
                </ul>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
