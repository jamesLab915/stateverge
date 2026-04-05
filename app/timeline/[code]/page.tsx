import Link from "next/link";
import { pool } from "@/lib/db";
import { getTimelineForCountry } from "@/lib/db/evolutionQueries";
import PowerTrendBadge from "@/components/power-trend-badge";

export const dynamic = "force-dynamic";

export default async function TimelinePage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  const c = await pool.query(`SELECT code, name FROM countries WHERE code = $1`, [code]);
  if (c.rows.length === 0) {
    return <div className="p-10">Country not found</div>;
  }

  const name = c.rows[0].name as string;
  const entries = await getTimelineForCountry(code, 60);

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-3xl">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-gray-500">
          Evolution timeline
        </p>
        <h1 className="mt-2 text-3xl font-bold">{name}</h1>
        <p className="mt-2 text-gray-600">
          Events and state snapshots in time order — how the board moved.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href={`/countries/${code}`}
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm"
          >
            Country profile
          </Link>
          <Link
            href={`/scenario/${code}`}
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm"
          >
            Scenarios
          </Link>
          <Link
            href={`/causal/${code}`}
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm"
          >
            Causal chain
          </Link>
        </div>

        <ol className="mt-12 space-y-6 border-l-2 border-gray-200 pl-6">
          {entries.map((e, i) => (
            <li key={`${e.kind}-${e.id}-${i}`} className="relative">
              <span className="absolute -left-[1.6rem] top-1 h-3 w-3 rounded-full bg-black" />
              <p className="text-xs text-gray-500">
                {e.sort_ts.slice(0, 10)} ·{" "}
                <span className="font-semibold text-gray-800">
                  {e.kind === "event" ? "Event" : "Snapshot"}
                </span>
              </p>
              <h2 className="mt-1 text-lg font-semibold">{e.title}</h2>
              <p className="mt-1 text-sm text-gray-700">{e.body}</p>
              <p className="mt-2 text-xs text-gray-500">{e.meta}</p>
              {e.kind === "snapshot" && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {e.power_trend && <PowerTrendBadge trend={e.power_trend} />}
                  {e.power_delta != null && (
                    <span className="text-xs text-gray-600">power Δ {e.power_delta}</span>
                  )}
                </div>
              )}
              {e.kind === "event" && (
                <p className="mt-2 text-xs text-gray-500">
                  Driver signal: {e.event_type} — use Causal page for dimension mapping.
                </p>
              )}
            </li>
          ))}
        </ol>

        {entries.length === 0 && (
          <p className="mt-10 text-gray-500">No events or snapshots yet for this country.</p>
        )}
      </div>
    </main>
  );
}
