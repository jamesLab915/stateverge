import Link from "next/link";
import {
  getActorBySlug,
  getPositionHistoryForActor,
  getEventsForActor,
  getFederalEventConsequences,
  getFederalAiCache,
  upsertFederalAiCache,
} from "@/lib/db/usFederalQueries";
import { generateFederalActorInsightAI } from "@/lib/ai/usFederalInsight";

export const dynamic = "force-dynamic";

export default async function USFederalActorDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  let actor: Awaited<ReturnType<typeof getActorBySlug>> = null;
  let history: Awaited<ReturnType<typeof getPositionHistoryForActor>> = [];
  let evs: Awaited<ReturnType<typeof getEventsForActor>> = [];
  let err: string | null = null;

  try {
    actor = await getActorBySlug(slug);
    if (actor) {
      [history, evs] = await Promise.all([
        getPositionHistoryForActor(actor.id),
        getEventsForActor(actor.id, 15),
      ]);
    }
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

  if (!actor) {
    return <main className="px-6 py-12">Actor not found</main>;
  }

  const cacheKey = `actor:${slug}`;
  let insight = (await getFederalAiCache(cacheKey)) as {
    headline?: string;
    current_role_summary?: string;
    power_status?: string;
    recent_actions?: string[];
    conflict_exposure?: string;
    strategic_takeaway?: string;
  } | null;

  if (!insight && process.env.OPENAI_API_KEY) {
    const ctx = JSON.stringify({ history: history.slice(0, 6), events: evs.slice(0, 8) }).slice(
      0,
      9000
    );
    try {
      insight = await generateFederalActorInsightAI(actor, ctx);
      await upsertFederalAiCache(cacheKey, insight as Record<string, unknown>);
    } catch {
      insight = null;
    }
  }

  const consequenceBlocks: { ev: (typeof evs)[0]; cons: Awaited<ReturnType<typeof getFederalEventConsequences>> }[] = [];
  for (const ev of evs.slice(0, 6)) {
    const row = ev as { id: number };
    const cons = (await getFederalEventConsequences(row.id)).filter(
      (c: { target_actor_id: number | null }) => c.target_actor_id === actor.id
    );
    if (cons.length) consequenceBlocks.push({ ev, cons });
  }

  return (
    <main className="min-h-screen bg-white px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <Link href="/us/actors" className="text-sm text-gray-500 hover:underline">
          ← Actors
        </Link>
        <h1 className="mt-4 text-3xl font-bold">{actor.name}</h1>
        <p className="text-sm text-gray-500">{actor.actor_type} · {actor.slug}</p>
        <p className="mt-4 text-gray-700">{actor.summary}</p>

        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Formal power", actor.formal_power_score, "Position-derived"],
            ["Political influence", actor.political_influence_score, "Event model"],
            ["Conflict index", actor.conflict_index, "Opposition / stress"],
            ["X signal (aux)", actor.x_signal_score ?? "—", "Not formal power"],
          ].map(([label, val, hint]) => (
            <div key={String(label)} className="rounded-xl border border-gray-200 p-4">
              <p className="text-xs uppercase text-gray-500">{label}</p>
              <p className="mt-1 text-2xl font-bold">{val ?? "—"}</p>
              <p className="mt-1 text-xs text-gray-500">{hint}</p>
            </div>
          ))}
        </div>

        <p className="mt-4 text-sm">
          <span className="font-medium">Power status (rules):</span>{" "}
          {actor.power_status ?? "—"}
        </p>

        {insight && (
          <section className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/50 p-6">
            <h2 className="text-lg font-semibold">AI insight (federal_ai_cache)</h2>
            <p className="mt-2 text-xl font-bold">{insight.headline}</p>
            <p className="mt-2 text-sm">{insight.current_role_summary}</p>
            <p className="mt-2 text-sm text-gray-700">Status: {insight.power_status}</p>
            <ul className="mt-4 list-disc pl-5 text-sm">
              {(insight.recent_actions || []).map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
            <p className="mt-4 text-sm">{insight.conflict_exposure}</p>
            <p className="mt-4 text-sm font-medium">{insight.strategic_takeaway}</p>
          </section>
        )}

        <section className="mt-10">
          <h2 className="text-lg font-semibold">Current / past positions</h2>
          <ul className="mt-4 space-y-3 text-sm">
            {history.map((h) => (
              <li key={h.id} className="rounded-lg border border-gray-100 p-3">
                <p className="font-medium">
                  {(h as { office_title: string }).office_title}
                </p>
                <p className="text-gray-600">
                  {(h as { start_date: string }).start_date} →{" "}
                  {(h as { end_date: string | null }).end_date ?? "present"} ·{" "}
                  {(h as { status: string }).status}
                </p>
                <p className="text-xs text-gray-500">
                  Source: {(h as { source_type: string }).source_type} · Confidence:{" "}
                  {(h as { confidence: string }).confidence}
                </p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-10">
          <h2 className="text-lg font-semibold">Recent federal events (involvement)</h2>
          <div className="mt-4 space-y-4">
            {evs.map((row) => {
              const r = row as Record<string, unknown>;
              return (
                <div key={String(r.id)} className="rounded-xl border border-gray-200 p-4 text-sm">
                  <p className="font-semibold">{r.title as string}</p>
                  <p className="text-xs text-gray-500">
                    {String(r.event_date)} · {String(r.confidence)} · {String(r.source_type)}
                  </p>
                  <p className="mt-2 text-gray-700">{r.summary as string}</p>
                  <p className="mt-2 text-xs">
                    Role: <span className="font-medium">{String(r.role)}</span> · Stance:{" "}
                    {String(r.stance ?? "—")}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        <section className="mt-10">
          <h2 className="text-lg font-semibold">Causal consequences (where this actor is target)</h2>
          <div className="mt-4 space-y-4 text-sm">
            {consequenceBlocks.length === 0 ? (
              <p className="text-gray-500">No consequence rows targeting this actor in sample.</p>
            ) : (
              consequenceBlocks.map(({ ev, cons }) => (
                <div key={String(ev.id)} className="rounded-lg border p-3">
                  <p className="font-medium">{String(ev.title)}</p>
                  <ul className="mt-2 list-disc pl-5">
                    {cons.map((c: { dimension: string; impact_value: number; confidence: string; explanation: string }) => (
                      <li key={c.dimension + String(c.impact_value)}>
                        {c.dimension}: {c.impact_value} · {c.confidence} — {c.explanation}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
