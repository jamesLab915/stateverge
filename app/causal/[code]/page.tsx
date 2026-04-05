import Link from "next/link";
import { pool } from "@/lib/db";
import {
  getEventsWithCausalForCountry,
  getCachedInsight,
} from "@/lib/db/evolutionQueries";
import { buildRuleCausalSummary } from "@/lib/evolution/ruleCausal";

export const dynamic = "force-dynamic";

export default async function CausalPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  const c = await pool.query(
    `
    SELECT c.name, s.overall, s.power_score
    FROM countries c
    JOIN country_scores s ON s.country_code = c.code
    WHERE c.code = $1
    `,
    [code]
  );

  if (c.rows.length === 0) {
    return <div className="p-10">Country not found</div>;
  }

  const name = c.rows[0].name as string;
  const overall = Number(c.rows[0].overall);
  const power =
    c.rows[0].power_score != null ? Number(c.rows[0].power_score) : null;

  const events = await getEventsWithCausalForCountry(code, 15);
  const rules = buildRuleCausalSummary(name, events, overall, power);

  const aiRaw = await getCachedInsight(`causal:${code}`);
  const ai = aiRaw as
    | {
        current_state_summary?: string;
        major_causes?: {
          event: string;
          dimension: string;
          impact: string;
          why_it_matters: string;
        }[];
        actor_summary?: string[];
      }
    | null;

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-4xl">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-gray-500">
          Causal analysis v1
        </p>
        <h1 className="mt-2 text-3xl font-bold">{name}</h1>
        <p className="mt-2 text-gray-600">
          What moved which dimensions, and who showed up on the board.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link href={`/countries/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Profile
          </Link>
          <Link href={`/timeline/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Timeline
          </Link>
          <Link href={`/scenario/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Scenarios
          </Link>
        </div>

        <div className="mt-12 grid gap-10 md:grid-cols-2">
          <section>
            <h2 className="text-lg font-semibold">Rule-based causal summary</h2>
            <p className="mt-3 text-sm text-gray-700">{rules.current_state_summary}</p>
            <h3 className="mt-6 font-semibold">Major channels</h3>
            <ul className="mt-3 space-y-4 text-sm">
              {rules.major_causes.map((m, i) => (
                <li key={i} className="rounded-xl border border-gray-100 p-4">
                  <p className="font-medium">{m.event}</p>
                  <p className="text-gray-600">
                    <span className="text-gray-800">{m.dimension}</span> · {m.impact}
                  </p>
                  <p className="mt-2 text-gray-600">{m.why_it_matters}</p>
                </li>
              ))}
            </ul>
            <h3 className="mt-8 font-semibold">Actors</h3>
            <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
              {rules.actor_summary.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold">Cached AI causal layer</h2>
            {!ai?.current_state_summary ? (
              <p className="mt-3 text-sm text-gray-500">
                No cache yet. Run{" "}
                <code className="text-xs">node scripts/generateCausalSummaries.js</code> with
                OPENAI_API_KEY set.
              </p>
            ) : (
              <>
                <p className="mt-3 text-sm text-gray-700">{ai.current_state_summary}</p>
                <h3 className="mt-6 font-semibold">Major causes (AI)</h3>
                <ul className="mt-3 space-y-4 text-sm">
                  {(ai.major_causes || []).map((m, i) => (
                    <li key={i} className="rounded-xl border border-gray-100 p-4">
                      <p className="font-medium">{m.event}</p>
                      <p className="text-gray-600">
                        {m.dimension} · {m.impact}
                      </p>
                      <p className="mt-2 text-gray-600">{m.why_it_matters}</p>
                    </li>
                  ))}
                </ul>
                <h3 className="mt-8 font-semibold">Actor summary (AI)</h3>
                <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
                  {(ai.actor_summary || []).map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>

        <section className="mt-14">
          <h2 className="text-lg font-semibold">Event → consequence detail</h2>
          <div className="mt-4 space-y-6">
            {events.map((e) => (
              <div key={e.id} className="rounded-2xl border border-gray-200 p-5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="font-semibold">{e.title}</h3>
                  <span className="text-xs text-gray-500">
                    {String(e.event_date).slice(0, 10)} · {e.impact_direction}{" "}
                    {e.impact_strength}
                  </span>
                </div>
                <p className="mt-2 text-sm text-gray-600">{e.summary}</p>
                {e.actors.length > 0 && (
                  <p className="mt-3 text-xs font-medium text-gray-700">
                    Actors:{" "}
                    {e.actors.map((a) => `${a.name} (${a.role})`).join(" · ")}
                  </p>
                )}
                <ul className="mt-3 space-y-2 text-sm text-gray-700">
                  {e.consequences.length === 0 ? (
                    <li className="text-gray-500">No structured consequences — legacy event.</li>
                  ) : (
                    e.consequences.map((c, i) => (
                      <li key={i}>
                        <span className="font-medium">{c.dimension}</span>{" "}
                        {c.impact_value > 0 ? "+" : ""}
                        {c.impact_value} ({c.time_horizon}, conf {c.confidence}) —{" "}
                        {c.explanation || "—"}
                      </li>
                    ))
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
