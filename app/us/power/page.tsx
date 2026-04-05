import Link from "next/link";
import {
  getActorsByFormalPower,
  getActorsByInfluence,
  getMostContestedActors,
  getLatestFederalEvents,
  getLatestFederalSnapshot,
  getEventActorsForEvent,
  getFederalAiCache,
  upsertFederalAiCache,
} from "@/lib/db/usFederalQueries";
import { generateFederalOverviewAI } from "@/lib/ai/usFederalInsight";

export const dynamic = "force-dynamic";

export default async function USFederalPowerPage() {
  let topFormal: Awaited<ReturnType<typeof getActorsByFormalPower>> = [];
  let topInfl: Awaited<ReturnType<typeof getActorsByInfluence>> = [];
  let contested: Awaited<ReturnType<typeof getMostContestedActors>> = [];
  let events: Awaited<ReturnType<typeof getLatestFederalEvents>> = [];
  let snapshot: Awaited<ReturnType<typeof getLatestFederalSnapshot>> = null;
  let loadError: string | null = null;

  try {
    [topFormal, topInfl, contested, events, snapshot] = await Promise.all([
      getActorsByFormalPower(12),
      getActorsByInfluence(12),
      getMostContestedActors(10),
      getLatestFederalEvents(8),
      getLatestFederalSnapshot(),
    ]);
  } catch (e) {
    loadError = e instanceof Error ? e.message : "Database error";
  }

  const cabinet = topFormal.filter(
    (a) =>
      a.actor_type === "cabinet" ||
      (a.office_title || "").toLowerCase().includes("secretary")
  );

  let overview = (await getFederalAiCache("us-overview")) as {
    headline?: string;
    stability?: string;
    key_power_centers?: string[];
    major_conflicts?: string[];
    strategic_takeaway?: string;
  } | null;

  if (!overview && process.env.OPENAI_API_KEY && !loadError) {
    const facts = JSON.stringify({
      snapshot,
      topFormal: topFormal.slice(0, 5),
      contested: contested.slice(0, 4),
      recentEvents: events.slice(0, 5),
    }).slice(0, 10000);
    try {
      overview = await generateFederalOverviewAI(facts);
      await upsertFederalAiCache("us-overview", overview as Record<string, unknown>);
    } catch {
      overview = null;
    }
  }

  const eventsWithActors = [];
  for (const e of events) {
    const ax = await getEventActorsForEvent(e.id);
    eventsWithActors.push({ e, ax });
  }

  if (loadError) {
    return (
      <main className="px-6 py-12">
        <p className="text-red-600">
          US Federal module: {loadError}. Run{" "}
          <code className="text-sm">node scripts/migrateUSFederalPowerTracking.js</code> and{" "}
          <code className="text-sm">node scripts/seedUSFederalV1.js</code>.
        </p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-6xl">
        <h1 className="text-3xl font-bold">US Federal power overview</h1>
        <p className="mt-2 max-w-3xl text-sm text-gray-600">
          Formal power derives from positions (weights), not from AI or X. Influence and
          conflict indices are modelled from events and consequences with confidence tags.
        </p>

        {snapshot && (
          <section className="mt-10 grid gap-4 rounded-2xl border border-gray-200 bg-slate-50 p-6 md:grid-cols-3 lg:grid-cols-6">
            {[
              ["Stability", snapshot.overall_power_stability],
              ["Exec. cohesion", snapshot.executive_cohesion],
              ["Cabinet", snapshot.cabinet_stability],
              ["Leg. alignment", snapshot.legislative_alignment],
              ["Conflict temp.", snapshot.conflict_temperature],
              ["Narrative pressure", snapshot.narrative_pressure],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <p className="text-xs uppercase text-gray-500">{k}</p>
                <p className="text-2xl font-bold">{v ?? "—"}</p>
              </div>
            ))}
            {snapshot.note && (
              <p className="md:col-span-3 lg:col-span-6 text-sm text-gray-600">
                {snapshot.note}
              </p>
            )}
          </section>
        )}

        {overview && (
          <section className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-6">
            <h2 className="text-lg font-semibold text-indigo-950">AI overview (cached)</h2>
            <p className="mt-2 text-xl font-bold">{overview.headline}</p>
            <p className="mt-2 text-sm text-gray-800">{overview.stability}</p>
            <div className="mt-4 grid gap-6 md:grid-cols-2 text-sm">
              <div>
                <p className="font-medium text-gray-700">Power centers</p>
                <ul className="mt-2 list-disc pl-5">
                  {(overview.key_power_centers || []).map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="font-medium text-gray-700">Conflict lines</p>
                <ul className="mt-2 list-disc pl-5">
                  {(overview.major_conflicts || []).map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="mt-4 text-sm font-medium text-gray-900">
              {overview.strategic_takeaway}
            </p>
          </section>
        )}

        <div className="mt-12 grid gap-10 lg:grid-cols-3">
          <section>
            <h2 className="text-lg font-semibold">Top formal power</h2>
            <p className="text-xs text-gray-500">From position weights (truth layer)</p>
            <ul className="mt-4 space-y-3 text-sm">
              {topFormal.map((a) => (
                <li key={a.id} className="flex justify-between gap-2 border-b border-gray-100 pb-2">
                  <Link href={`/us/actors/${a.slug}`} className="font-medium hover:underline">
                    {a.name}
                  </Link>
                  <span className="shrink-0 text-gray-700">{a.formal_power_score ?? "—"}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold">Top political influence</h2>
            <p className="text-xs text-gray-500">Modelled (events + roles), not formal rank</p>
            <ul className="mt-4 space-y-3 text-sm">
              {topInfl.map((a) => (
                <li key={a.id} className="flex justify-between gap-2 border-b border-gray-100 pb-2">
                  <Link href={`/us/actors/${a.slug}`} className="font-medium hover:underline">
                    {a.name}
                  </Link>
                  <span className="shrink-0">{a.political_influence_score ?? "—"}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold">Most contested</h2>
            <p className="text-xs text-gray-500">Conflict index from opposing / removal signals</p>
            <ul className="mt-4 space-y-3 text-sm">
              {contested.map((a) => (
                <li key={a.id} className="flex justify-between gap-2 border-b border-gray-100 pb-2">
                  <Link href={`/us/actors/${a.slug}`} className="font-medium hover:underline">
                    {a.name}
                  </Link>
                  <span className="shrink-0 text-rose-700">{a.conflict_index ?? "—"}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className="mt-14">
          <h2 className="text-lg font-semibold">Cabinet / executive slice</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(cabinet.length ? cabinet : topFormal.filter((a) => a.branch === "executive")).map(
              (a) => (
                <Link
                  key={a.id}
                  href={`/us/actors/${a.slug}`}
                  className="rounded-xl border border-gray-200 p-4 hover:bg-gray-50"
                >
                  <p className="font-medium">{a.name}</p>
                  <p className="text-xs text-gray-500">{a.office_title}</p>
                  <p className="mt-2 text-sm">
                    Formal {a.formal_power_score ?? "—"} · Influence{" "}
                    {a.political_influence_score ?? "—"}
                  </p>
                </Link>
              )
            )}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-lg font-semibold">Latest major federal events</h2>
          <div className="mt-4 space-y-4">
            {eventsWithActors.map(({ e, ax }) => (
              <div key={e.id} className="rounded-xl border border-gray-200 p-4 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <Link href="/us/events" className="font-semibold hover:underline">
                    {e.title}
                  </Link>
                  <span className="text-xs text-gray-500">
                    {e.event_date} · {e.confidence} · {e.source_type}
                  </span>
                </div>
                <p className="mt-2 text-gray-600">{e.summary}</p>
                <p className="mt-2 text-xs text-gray-500">
                  Actors:{" "}
                  {ax.length
                    ? ax
                        .map(
                          (r: { name: string; role: string }) =>
                            `${r.name} (${r.role})`
                        )
                        .join(" · ")
                    : "—"}
                </p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
