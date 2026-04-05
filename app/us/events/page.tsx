import { getEventsWithActorNames } from "@/lib/db/usFederalQueries";

export const dynamic = "force-dynamic";

export default async function USFederalEventsPage() {
  let rows: Awaited<ReturnType<typeof getEventsWithActorNames>> = [];
  let err: string | null = null;
  try {
    rows = await getEventsWithActorNames(60);
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

  return (
    <main className="min-h-screen bg-white px-6 py-10">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-3xl font-bold">Federal events</h1>
        <p className="mt-2 text-sm text-gray-600">
          Source types include official, departmental, court, congressional, reputable_media,
          x_signal (reserved), inferred. Confidence: confirmed / contested / speculative.
        </p>

        <div className="mt-8 space-y-4">
          {rows.map((e) => (
            <article
              key={e.id}
              id={`event-${e.id}`}
              className="rounded-2xl border border-gray-200 p-5 text-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h2 className="text-lg font-semibold">{e.title}</h2>
                <span className="text-xs text-gray-500">{e.event_date}</span>
              </div>
              <p className="mt-2 text-gray-700">{e.summary}</p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-gray-100 px-2 py-1">{e.event_type}</span>
                <span className="rounded-full bg-gray-100 px-2 py-1">{e.branch ?? "—"}</span>
                <span className="rounded-full bg-gray-100 px-2 py-1">{e.department ?? "—"}</span>
                <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-900">
                  {e.source_type}
                </span>
                <span
                  className={`rounded-full px-2 py-1 ${
                    e.confidence === "confirmed"
                      ? "bg-emerald-50 text-emerald-900"
                      : e.confidence === "contested"
                        ? "bg-amber-50 text-amber-900"
                        : "bg-rose-50 text-rose-900"
                  }`}
                >
                  {e.confidence}
                </span>
              </div>
              <p className="mt-2 text-xs text-gray-600">
                Impact: {e.impact_direction ?? "—"} / strength {e.impact_strength ?? "—"}
              </p>
              <p className="mt-2 text-xs text-gray-500">
                Actors: {e.actors_label || "—"}
              </p>
              {e.source_name && (
                <p className="mt-1 text-xs text-gray-400">Via {e.source_name}</p>
              )}
            </article>
          ))}
        </div>

        {rows.length === 0 && (
          <p className="mt-8 text-gray-500">No events. Seed the module.</p>
        )}
      </div>
    </main>
  );
}
