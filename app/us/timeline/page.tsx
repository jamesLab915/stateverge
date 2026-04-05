import { getFederalTimelineMerged } from "@/lib/db/usFederalQueries";

export const dynamic = "force-dynamic";

export default async function USFederalTimelinePage() {
  let items: Awaited<ReturnType<typeof getFederalTimelineMerged>> = [];
  let err: string | null = null;
  try {
    items = await getFederalTimelineMerged(55);
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
      <div className="mx-auto max-w-3xl">
        <h1 className="text-3xl font-bold">US federal timeline</h1>
        <p className="mt-2 text-sm text-gray-600">
          Snapshots, federal events, and position changes — merged from PostgreSQL only.
        </p>

        <ol className="mt-10 space-y-6 border-l-2 border-slate-200 pl-6">
          {items.map((it, i) => (
            <li key={`${it.kind}-${it.date}-${i}`} className="relative">
              <span className="absolute -left-[1.55rem] top-1 h-2.5 w-2.5 rounded-full bg-slate-800" />
              <p className="text-xs text-gray-500">
                {it.date} ·{" "}
                <span className="font-semibold capitalize text-gray-800">{it.kind}</span>
              </p>
              <h2 className="mt-1 text-base font-semibold">{it.label}</h2>
              <p className="mt-1 text-sm text-gray-700">{it.body}</p>
              <p className="mt-2 text-xs text-gray-500">{it.meta}</p>
            </li>
          ))}
        </ol>

        {items.length === 0 && (
          <p className="mt-10 text-gray-500">No timeline rows. Run seed.</p>
        )}
      </div>
    </main>
  );
}
