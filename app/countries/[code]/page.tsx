import Link from "next/link";
import { pool } from "@/lib/db";
import { generateCountryInsight } from "@/lib/ai/generateInsight";
import { generateEvolutionSummaryAI } from "@/lib/ai/evolutionInsights";
import {
  getTimelineForCountry,
  getCachedInsight,
  upsertInsightCache,
} from "@/lib/db/evolutionQueries";
import PowerTrendBadge from "@/components/power-trend-badge";
import { powerTrendFromScores } from "@/lib/power";
import { buildCountryEvolutionNarrative } from "@/lib/evolution/narrativeEngine.js";
import {
  polishEvolutionNarrativeJSON,
  type EvolutionNarrativeRecord,
} from "@/lib/ai/evolutionNarrativePolish";
import { buildCountryNarrativeGraph } from "@/lib/evolution/narrativeGraph.js";
import {
  polishCountryNarrativeGraphText,
  type CountryNarrativeGraphPayload,
} from "@/lib/ai/evolutionNarrativeGraphPolish";

export const dynamic = "force-dynamic";

type EventRow = {
  id: number;
  event_type: string;
  title: string;
  summary: string;
  event_date: string;
  impact_direction: string;
  impact_strength: number;
  source_name: string | null;
};

function buildPowerDriverHints(
  country: Record<string, unknown>,
  events: EventRow[]
): string[] {
  const dims = [
    { label: "Economy", v: Number(country.economy ?? 0) },
    { label: "Innovation", v: Number(country.innovation ?? 0) },
    { label: "Infrastructure", v: Number(country.infrastructure ?? 0) },
    { label: "Governance", v: Number(country.governance ?? 0) },
    { label: "Openness", v: Number(country.openness ?? 0) },
    { label: "Future potential", v: Number(country.future_potential ?? 0) },
  ].sort((a, b) => b.v - a.v);

  const lines: string[] = [];
  for (const d of dims.slice(0, 3)) {
    lines.push(`${d.label} score ${d.v} — structural weight in power model.`);
  }
  for (const ev of events.slice(0, 3)) {
    lines.push(
      `Event: ${ev.title} (${ev.impact_direction}, strength ${ev.impact_strength}).`
    );
  }
  return lines.slice(0, 6);
}

export default async function CountryDetailPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  const countryResult = await pool.query(
    `
    SELECT
      c.code,
      c.name,
      c.region,
      c.capital,
      c.population,
      c.gdp,
      c.summary,
      s.governance,
      s.social_order,
      s.economy,
      s.human_capital,
      s.infrastructure,
      s.innovation,
      s.openness,
      s.future_potential,
      s.overall,
      s.risk,
      s.opportunity,
      s.power_score,
      s.power_classification
    FROM countries c
    JOIN country_scores s
      ON c.code = s.country_code
    WHERE c.code = $1
  `,
    [code]
  );

  const country = countryResult.rows[0];

  if (!country) {
    return <div className="p-10">Country not found</div>;
  }

  const eventsResult = await pool.query(
    `
    SELECT
      id,
      event_type,
      title,
      summary,
      event_date,
      impact_direction,
      impact_strength,
      source_name
    FROM events
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT 5
  `,
    [code]
  );

  const events = eventsResult.rows as EventRow[];

  const snapshotResult = await pool.query(
    `
    SELECT
      id,
      overall,
      governance,
      social_order,
      economy,
      human_capital,
      infrastructure,
      innovation,
      openness,
      future_potential,
      risk,
      opportunity,
      power_score,
      power_delta,
      power_trend,
      power_note,
      note
    FROM country_score_snapshots
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT 2
  `,
    [code]
  );

  const latestSnapshot = snapshotResult.rows[0] || null;
  const previousSnapshot = snapshotResult.rows[1] || null;

  const prevPower =
    previousSnapshot != null && previousSnapshot.power_score != null
      ? Number(previousSnapshot.power_score)
      : null;
  const nextPower =
    latestSnapshot != null && latestSnapshot.power_score != null
      ? Number(latestSnapshot.power_score)
      : null;

  const derivedTrend =
    latestSnapshot?.power_trend != null
      ? String(latestSnapshot.power_trend)
      : powerTrendFromScores(prevPower, nextPower).power_trend;

  const powerDeltaDisplay =
    latestSnapshot?.power_delta != null
      ? Number(latestSnapshot.power_delta)
      : prevPower != null && nextPower != null
        ? Math.round((nextPower - prevPower) * 100) / 100
        : null;

  const driverHints = buildPowerDriverHints(country, events);

  const insight = await generateCountryInsight({
    ...country,
    overall: country.overall,
  });

  const timeline = await getTimelineForCountry(code, 20);
  const evoHint = timeline
    .slice(0, 10)
    .map((t) => `${t.kind}: ${t.title}`)
    .join(" | ");

  let evolutionSummary: Awaited<
    ReturnType<typeof generateEvolutionSummaryAI>
  > | null = null;
  if (process.env.OPENAI_API_KEY) {
    try {
      evolutionSummary = await generateEvolutionSummaryAI(
        country.name as string,
        evoHint.slice(0, 3500)
      );
    } catch {
      evolutionSummary = null;
    }
  }

  const ruleNarrative = await buildCountryEvolutionNarrative(pool, code);
  const evolutionCacheKey = `evolution-summary:${code}`;
  let evolutionNarrative: EvolutionNarrativeRecord = {
    summary: ruleNarrative.summary,
    direction: ruleNarrative.direction,
    turning_points: ruleNarrative.turning_points,
    key_drivers: ruleNarrative.key_drivers,
    risks: ruleNarrative.risks,
  };
  const cachedNarr = await getCachedInsight(evolutionCacheKey);
  if (
    cachedNarr &&
    typeof cachedNarr.summary === "string" &&
    Array.isArray(cachedNarr.turning_points)
  ) {
    evolutionNarrative = {
      ...(cachedNarr as unknown as EvolutionNarrativeRecord),
      direction: ruleNarrative.direction,
    };
  } else if (process.env.OPENAI_API_KEY) {
    try {
      const polished = await polishEvolutionNarrativeJSON(evolutionNarrative);
      evolutionNarrative = { ...polished, direction: ruleNarrative.direction };
      await upsertInsightCache(
        evolutionCacheKey,
        evolutionNarrative as unknown as Record<string, unknown>
      );
    } catch {
      evolutionNarrative = {
        summary: ruleNarrative.summary,
        direction: ruleNarrative.direction,
        turning_points: ruleNarrative.turning_points,
        key_drivers: ruleNarrative.key_drivers,
        risks: ruleNarrative.risks,
      };
    }
  }

  const ruleNarrativeGraph = (await buildCountryNarrativeGraph(
    pool,
    code
  )) as CountryNarrativeGraphPayload;
  const narrativeGraphCacheKey = `narrative-graph:${code}`;
  const cachedNarrativeGraph = await getCachedInsight(narrativeGraphCacheKey);

  function mergeKeyPathsFromCache(
    rulePaths: CountryNarrativeGraphPayload["key_paths"],
    cached: unknown
  ): CountryNarrativeGraphPayload["key_paths"] {
    if (!Array.isArray(cached)) return rulePaths;
    const byPath = new Map<string, string>();
    for (const row of cached) {
      if (
        row &&
        typeof row === "object" &&
        Array.isArray((row as { path?: unknown }).path) &&
        typeof (row as { why_it_matters?: unknown }).why_it_matters === "string"
      ) {
        const r = row as { path: string[]; why_it_matters: string };
        byPath.set(JSON.stringify(r.path), r.why_it_matters);
      }
    }
    return rulePaths.map((kp) => ({
      ...kp,
      why_it_matters: byPath.get(JSON.stringify(kp.path)) ?? kp.why_it_matters,
    }));
  }

  let narrativeGraphDisplay: {
    summary: string;
    key_paths: CountryNarrativeGraphPayload["key_paths"];
  } = {
    summary: ruleNarrativeGraph.summary,
    key_paths: ruleNarrativeGraph.key_paths,
  };

  if (
    cachedNarrativeGraph &&
    typeof cachedNarrativeGraph.summary === "string" &&
    Array.isArray(cachedNarrativeGraph.key_paths)
  ) {
    narrativeGraphDisplay = {
      summary: cachedNarrativeGraph.summary as string,
      key_paths: mergeKeyPathsFromCache(
        ruleNarrativeGraph.key_paths,
        cachedNarrativeGraph.key_paths
      ),
    };
  } else if (process.env.OPENAI_API_KEY && ruleNarrativeGraph.nodes.length > 0) {
    try {
      const polished = await polishCountryNarrativeGraphText(ruleNarrativeGraph);
      narrativeGraphDisplay = {
        summary: polished.summary,
        key_paths: polished.key_paths,
      };
      await upsertInsightCache(narrativeGraphCacheKey, {
        summary: polished.summary,
        key_paths: polished.key_paths,
      });
    } catch {
      narrativeGraphDisplay = {
        summary: ruleNarrativeGraph.summary,
        key_paths: ruleNarrativeGraph.key_paths,
      };
    }
  }

  const metrics = [
    { label: "Governance", value: country.governance ?? 0 },
    { label: "Social Order", value: country.social_order ?? 0 },
    { label: "Economy", value: country.economy ?? 0 },
    { label: "Human Capital", value: country.human_capital ?? 0 },
    { label: "Infrastructure", value: country.infrastructure ?? 0 },
    { label: "Innovation", value: country.innovation ?? 0 },
    { label: "Openness", value: country.openness ?? 0 },
    { label: "Future Potential", value: country.future_potential ?? 0 },
  ];

  const scoreChanges = latestSnapshot && previousSnapshot
    ? [
        {
          label: "Overall",
          current: latestSnapshot.overall,
          previous: previousSnapshot.overall,
        },
        {
          label: "Power",
          current: latestSnapshot.power_score,
          previous: previousSnapshot.power_score,
        },
        {
          label: "Governance",
          current: latestSnapshot.governance,
          previous: previousSnapshot.governance,
        },
        {
          label: "Economy",
          current: latestSnapshot.economy,
          previous: previousSnapshot.economy,
        },
        {
          label: "Innovation",
          current: latestSnapshot.innovation,
          previous: previousSnapshot.innovation,
        },
        {
          label: "Risk",
          current: latestSnapshot.risk,
          previous: previousSnapshot.risk,
        },
        {
          label: "Opportunity",
          current: latestSnapshot.opportunity,
          previous: previousSnapshot.opportunity,
        },
      ]
    : [];

  const powerDriversDisplay = driverHints.slice(0, 4);

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-4xl font-bold">{country.name}</h1>
        <p className="mt-2 text-gray-500">{country.region}</p>

        <p className="mt-6 text-lg leading-8 text-gray-700">
          {country.summary}
        </p>

        <div className="mt-8 flex flex-wrap gap-4">
          <Link
            href="/countries"
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm"
          >
            Back to Countries
          </Link>

          <Link
            href="/power"
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm"
          >
            Power board
          </Link>

          <Link
            href={`/compare/${country.code}-us`}
            className="rounded-xl bg-black px-4 py-2 text-sm text-white"
          >
            Compare with USA
          </Link>
        </div>

        <section className="mt-10 rounded-2xl border border-indigo-200 bg-indigo-50/40 p-6">
          <h2 className="text-lg font-semibold text-indigo-950">Evolution Engine</h2>
          <p className="mt-2 text-sm text-indigo-900/80">
            Track time, scenarios, and causal chains for this country.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link
              href={`/timeline/${code}`}
              className="rounded-xl bg-indigo-900 px-4 py-2 text-sm text-white"
            >
              Timeline
            </Link>
            <Link
              href={`/scenario/${code}`}
              className="rounded-xl border border-indigo-300 bg-white px-4 py-2 text-sm text-indigo-950"
            >
              Scenarios
            </Link>
            <Link
              href={`/causal/${code}`}
              className="rounded-xl border border-indigo-300 bg-white px-4 py-2 text-sm text-indigo-950"
            >
              Causal analysis
            </Link>
          </div>

          <div className="mt-8 border-t border-indigo-200 pt-6">
            <h3 className="font-semibold text-indigo-950">Recent evolution summary</h3>
            {evolutionSummary ? (
              <div className="mt-3 space-y-3 text-sm text-gray-800">
                <p className="text-lg font-bold">{evolutionSummary.headline}</p>
                <p>{evolutionSummary.arc}</p>
                <ul className="list-disc space-y-1 pl-5">
                  {(evolutionSummary.inflections || []).map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
                <p className="text-gray-700">{evolutionSummary.forward_watch}</p>
              </div>
            ) : (
              <div className="mt-3 text-sm text-gray-700">
                <p>
                  {timeline.length} timeline entries (events + snapshots). Open{" "}
                  <Link href={`/timeline/${code}`} className="underline">
                    Timeline
                  </Link>{" "}
                  for the full chain. With OPENAI_API_KEY, this block auto-generates a
                  structured arc.
                </p>
                <ul className="mt-3 list-disc space-y-1 pl-5 text-gray-600">
                  {timeline.slice(0, 5).map((t, i) => (
                    <li key={i}>
                      <span className="font-medium">{t.kind}</span>: {t.title}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>

        <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-2">
          <div className="rounded-xl border p-4">
            <p className="text-sm text-gray-500">Capital</p>
            <p className="text-lg font-medium">{country.capital}</p>
          </div>

          <div className="rounded-xl border p-4">
            <p className="text-sm text-gray-500">Population</p>
            <p className="text-lg font-medium">
              {Number(country.population).toLocaleString()}
            </p>
          </div>

          <div className="rounded-xl border p-4">
            <p className="text-sm text-gray-500">GDP</p>
            <p className="text-lg font-medium">
              ${Number(country.gdp).toLocaleString()}
            </p>
          </div>

          <div className="rounded-xl border p-4">
            <p className="text-sm text-gray-500">Overall Score</p>
            <p className="text-lg font-medium">{country.overall}</p>
          </div>
        </div>

        <div className="mt-8 flex flex-wrap gap-4">
          <span className="rounded-full bg-gray-100 px-4 py-2">
            Risk {country.risk}
          </span>
          <span className="rounded-full bg-gray-100 px-4 py-2">
            Opportunity {country.opportunity}
          </span>
        </div>

        <section className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Power Tracking</h2>
          <p className="mt-2 text-sm text-gray-600">
            Composite power (independent of overall): weighted economy, innovation,
            infrastructure, governance, openness, future potential.
          </p>

          <div className="mt-6 grid gap-4 md:grid-cols-3">
            <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
              <p className="text-xs font-medium uppercase text-gray-500">
                Power score
              </p>
              <p className="mt-1 text-3xl font-bold">
                {country.power_score != null ? country.power_score : "—"}
              </p>
              <p className="mt-2 text-sm text-gray-600">
                {country.power_classification ?? "—"}
              </p>
            </div>

            <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
              <p className="text-xs font-medium uppercase text-gray-500">
                Power trend
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <PowerTrendBadge trend={derivedTrend} />
                <span className="text-sm text-gray-600">
                  Δ vs prior snapshot:{" "}
                  {powerDeltaDisplay != null ? powerDeltaDisplay : "—"}
                </span>
              </div>
              {latestSnapshot?.power_note && (
                <p className="mt-3 text-sm text-gray-600">
                  {latestSnapshot.power_note}
                </p>
              )}
            </div>

            <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
              <p className="text-xs font-medium uppercase text-gray-500">
                Classification
              </p>
              <p className="mt-2 text-lg font-semibold leading-snug">
                {country.power_classification ?? "Not classified"}
              </p>
            </div>
          </div>

          <div className="mt-8">
            <h3 className="font-semibold">Power drivers (signals)</h3>
            <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-gray-700">
              {powerDriversDisplay.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        </section>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Dimension Scores</h2>
          <div className="mt-6 space-y-4">
            {metrics.map((metric) => (
              <div key={metric.label}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span>{metric.label}</span>
                  <span className="font-medium">{metric.value}</span>
                </div>
                <div className="h-3 w-full rounded-full bg-gray-100">
                  <div
                    className="h-3 rounded-full bg-black"
                    style={{ width: `${metric.value}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">AI Evolution Insight</h2>
          <p className="mt-1 text-sm text-gray-500">
            Structured JSON: headline, trend, drivers, weaknesses, strategic takeaway.
          </p>

          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <p className="text-sm text-gray-500">Headline</p>
            <p className="mt-1 text-2xl font-bold">
              {insight.headline || "No headline generated."}
            </p>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-sm text-gray-600">Trend:</span>
            <PowerTrendBadge trend={insight.trend} />
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Drivers</h3>
            <ul className="mt-2 list-disc space-y-2 pl-5 text-gray-700">
              {(insight.drivers || []).map((item: string, index: number) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Weaknesses</h3>
            <ul className="mt-2 list-disc space-y-2 pl-5 text-gray-700">
              {(insight.weaknesses || []).map((item: string, index: number) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Strategic takeaway</h3>
            <p className="mt-2 text-gray-700">
              {insight.strategic_takeaway || "No insight generated."}
            </p>
          </div>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Latest Events</h2>

          <div className="mt-6 space-y-4">
            {events.length === 0 ? (
              <p className="text-gray-500">No recent events.</p>
            ) : (
              events.map((event) => (
                <div key={event.id} className="rounded-xl border border-gray-100 p-4">
                  <div className="flex items-center justify-between gap-4">
                    <h3 className="font-semibold">{event.title}</h3>
                    <span
                      className={`rounded-full px-3 py-1 text-xs ${
                        event.impact_direction === "positive"
                          ? "bg-green-100 text-green-700"
                          : "bg-red-100 text-red-700"
                      }`}
                    >
                      {event.impact_direction} {event.impact_strength}
                    </span>
                  </div>

                  <p className="mt-2 text-sm text-gray-600">{event.summary}</p>

                  <div className="mt-3 flex flex-wrap gap-3 text-xs text-gray-500">
                    <span>Type: {event.event_type}</span>
                    <span>Date: {String(event.event_date).slice(0, 10)}</span>
                    <span>Source: {event.source_name || "Unknown"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Recent Event Impact Summary</h2>
          {events.length === 0 ? (
            <p className="mt-4 text-gray-500">No events to summarize.</p>
          ) : (
            <ul className="mt-4 list-disc space-y-2 pl-5 text-sm text-gray-700">
              {events.map((event) => (
                <li key={event.id}>
                  <span className="font-medium">{event.title}</span> —{" "}
                  {event.impact_direction} impact ({event.impact_strength}):{" "}
                  {event.summary}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Latest Score Change</h2>

          {scoreChanges.length === 0 ? (
            <p className="mt-4 text-gray-500">Not enough snapshot history yet.</p>
          ) : (
            <div className="mt-6 space-y-4">
              {scoreChanges.map((item) => {
                const hasBoth =
                  item.current != null &&
                  item.current !== "" &&
                  item.previous != null &&
                  item.previous !== "";
                const cur = hasBoth ? Number(item.current) : NaN;
                const prev = hasBoth ? Number(item.previous) : NaN;
                const diff = hasBoth && !Number.isNaN(cur) && !Number.isNaN(prev)
                  ? Math.round((cur - prev) * 100) / 100
                  : null;
                const positive = diff != null && diff > 0;
                const neutral = diff == null || diff === 0;

                return (
                  <div key={item.label} className="rounded-xl border border-gray-100 p-4">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{item.label}</span>
                      <span
                        className={`text-sm font-semibold ${
                          neutral
                            ? "text-gray-500"
                            : positive
                              ? "text-green-600"
                              : "text-red-600"
                        }`}
                      >
                        {diff == null ? "—" : diff === 0 ? "0" : positive ? `+${diff}` : diff}
                      </span>
                    </div>
                    <div className="mt-2 text-sm text-gray-600">
                      {item.previous ?? "—"} → {item.current ?? "—"}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {latestSnapshot?.note && (
            <p className="mt-6 text-sm text-gray-500">
              Latest update note: {latestSnapshot.note}
            </p>
          )}
        </div>

        <section className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Evolution Summary</h2>
          <p className="mt-1 text-sm text-gray-500">
            Rule engine: snapshots, trends, event_consequences, and chains. Direction signal is
            always recomputed from data; optional wording may be cached in ai_insights_cache.
          </p>
          <div className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-gray-800">
            <p className="text-xs font-semibold uppercase text-gray-500">Direction signal</p>
            <p className="mt-1 text-lg font-bold capitalize text-gray-900">
              {evolutionNarrative.direction}
            </p>
          </div>
          <p className="mt-6 text-gray-800">{evolutionNarrative.summary}</p>
          <div className="mt-6">
            <h3 className="font-semibold">Key turning points</h3>
            <ul className="mt-3 list-disc space-y-3 pl-5 text-gray-700">
              {(evolutionNarrative.turning_points || []).map((tp, i) => (
                <li key={i}>
                  <span className="font-medium">{tp.event}</span>
                  <p className="text-sm text-gray-600">{tp.impact}</p>
                  <p className="mt-1 text-sm text-gray-500">{tp.why_important}</p>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <div>
              <h3 className="font-semibold">Key drivers</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
                {(evolutionNarrative.key_drivers || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">Risks</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
                {(evolutionNarrative.risks || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-12 rounded-2xl border border-dashed border-gray-200 p-6">
          <h2 className="text-lg font-semibold">Narrative Graph Summary</h2>
          <p className="mt-1 text-xs text-gray-500">
            Rule-built graph from PostgreSQL (nodes/edges computed server-side); wording may be
            cached under <code className="text-xs">{narrativeGraphCacheKey}</code>.
          </p>
          <p className="mt-4 text-sm text-gray-800">{narrativeGraphDisplay.summary}</p>
          <div className="mt-4">
            <h3 className="text-sm font-semibold text-gray-700">Key paths</h3>
            <ul className="mt-2 list-decimal space-y-2 pl-5 text-sm text-gray-700">
              {narrativeGraphDisplay.key_paths.slice(0, 3).map((kp, i) => (
                <li key={i}>
                  <span className="font-mono text-xs text-gray-600">
                    {kp.path.join(" → ")}
                  </span>
                  <p className="mt-1 text-gray-600">{kp.why_it_matters}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </main>
  );
}
