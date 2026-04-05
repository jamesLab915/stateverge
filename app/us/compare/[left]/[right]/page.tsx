import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getActorBySlug,
  countActorEventsSince,
  getFederalAiCache,
  upsertFederalAiCache,
} from "@/lib/db/usFederalQueries";
import {
  buildFederalActorCompareRules,
  federalActorCompareCacheKey,
} from "@/lib/evolution/usFederalActorCompareRules";
import { generateFederalActorCompareAI } from "@/lib/ai/usFederalInsight";

export const dynamic = "force-dynamic";

function sinceDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

export default async function USFederalActorComparePage({
  params,
}: {
  params: Promise<{ left: string; right: string }>;
}) {
  const { left, right } = await params;
  if (!left || !right || left === right) notFound();

  let err: string | null = null;
  let actorL: Awaited<ReturnType<typeof getActorBySlug>> = null;
  let actorR: Awaited<ReturnType<typeof getActorBySlug>> = null;
  let cntL = 0;
  let cntR = 0;

  const since = sinceDaysAgo(90);

  try {
    [actorL, actorR] = await Promise.all([getActorBySlug(left), getActorBySlug(right)]);
    if (!actorL || !actorR) {
      notFound();
    }
    [cntL, cntR] = await Promise.all([
      countActorEventsSince(actorL.id, since),
      countActorEventsSince(actorR.id, since),
    ]);
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

  if (!actorL || !actorR) notFound();

  const rules = buildFederalActorCompareRules({
    left: actorL,
    right: actorR,
    leftRecentEventCount: cntL,
    rightRecentEventCount: cntR,
  });

  const cacheKey = federalActorCompareCacheKey(left, right);

  let ai = (await getFederalAiCache(cacheKey)) as {
    compare_headline?: string;
    left_reading?: string;
    right_reading?: string;
    uncertainty_notes?: string[];
  } | null;

  if (!ai && process.env.OPENAI_API_KEY) {
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
    try {
      ai = await generateFederalActorCompareAI(JSON.stringify(rules), metricsJson);
      await upsertFederalAiCache(cacheKey, ai as Record<string, unknown>);
    } catch {
      ai = null;
    }
  }

  const rows = [
    ["Formal power", actorL.formal_power_score ?? "—", actorR.formal_power_score ?? "—"],
    ["Political influence", actorL.political_influence_score ?? "—", actorR.political_influence_score ?? "—"],
    ["Conflict index", actorL.conflict_index ?? "—", actorR.conflict_index ?? "—"],
    ["X signal (aux.)", actorL.x_signal_score ?? "—", actorR.x_signal_score ?? "—"],
    ["Agenda alignment", actorL.agenda_alignment_score ?? "—", actorR.agenda_alignment_score ?? "—"],
    [`Events tied (${since} →)`, cntL, cntR],
  ] as const;

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-5xl">
        <p className="text-xs text-gray-500">
          Cache key: <code>{cacheKey}</code>
        </p>
        <h1 className="mt-2 text-3xl font-bold">Federal actor compare</h1>
        <p className="mt-2 text-sm text-gray-600">
          Truth metrics from PostgreSQL. Rule summary is deterministic; AI insight is optional cached JSON only.
        </p>

        <div className="mt-10 grid gap-6 md:grid-cols-2">
          <div className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">
              <Link href={`/us/actors/${actorL.slug}`} className="hover:underline">
                {actorL.name}
              </Link>
            </h2>
            <p className="text-sm text-gray-600">{actorL.office_title}</p>
            <p className="mt-2 text-xs text-gray-500">{actorL.branch} · {actorL.department ?? "—"}</p>
            <p className="mt-2 text-xs">{actorL.power_status}</p>
          </div>
          <div className="rounded-2xl border border-gray-200 p-6">
            <h2 className="text-lg font-semibold">
              <Link href={`/us/actors/${actorR.slug}`} className="hover:underline">
                {actorR.name}
              </Link>
            </h2>
            <p className="text-sm text-gray-600">{actorR.office_title}</p>
            <p className="mt-2 text-xs text-gray-500">{actorR.branch} · {actorR.department ?? "—"}</p>
            <p className="mt-2 text-xs">{actorR.power_status}</p>
          </div>
        </div>

        <section className="mt-10 overflow-x-auto rounded-2xl border border-gray-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-4 py-3">Metric</th>
                <th className="px-4 py-3">{actorL.name}</th>
                <th className="px-4 py-3">{actorR.name}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(([label, a, b]) => (
                <tr key={label}>
                  <td className="px-4 py-3 font-medium text-gray-700">{label}</td>
                  <td className="px-4 py-3">{a}</td>
                  <td className="px-4 py-3">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="mt-10 rounded-2xl border border-slate-200 bg-slate-50 p-6">
          <h2 className="text-lg font-semibold">Rule-based compare summary</h2>
          <p className="mt-2 text-sm">
            <span className="font-medium">Winner (heuristic):</span> {rules.winner}
          </p>
          <p className="mt-3 text-sm text-gray-800">{rules.difference_summary}</p>
          <p className="mt-4 text-sm text-gray-800">{rules.role_contrast}</p>
          <div className="mt-6 grid gap-6 md:grid-cols-2 text-sm">
            <div>
              <p className="font-semibold text-gray-800">Core advantages</p>
              <ul className="mt-2 list-disc pl-5 text-gray-700">
                {rules.core_advantages.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="font-semibold text-gray-800">Core risks</p>
              <ul className="mt-2 list-disc pl-5 text-gray-700">
                {rules.core_risks.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          </div>
          <p className="mt-6 text-sm font-medium text-gray-900">{rules.bottom_line}</p>
        </section>

        {ai && (
          <section className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-6">
            <h2 className="text-lg font-semibold text-indigo-950">AI compare insight (cached)</h2>
            <p className="mt-2 text-xl font-bold">{ai.compare_headline}</p>
            <div className="mt-4 grid gap-4 text-sm md:grid-cols-2">
              <div>
                <p className="text-xs font-medium text-gray-500">{actorL.name}</p>
                <p className="mt-1 text-gray-800">{ai.left_reading}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500">{actorR.name}</p>
                <p className="mt-1 text-gray-800">{ai.right_reading}</p>
              </div>
            </div>
            <ul className="mt-4 list-disc pl-5 text-sm text-gray-700">
              {(ai.uncertainty_notes || []).map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </main>
  );
}
