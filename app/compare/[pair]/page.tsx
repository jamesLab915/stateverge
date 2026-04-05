import { generateAIInsight } from "@/lib/ai/generateInsight";
import { generateCompareCausalSummaryAI } from "@/lib/ai/evolutionInsights";
import {
  polishEvolutionContrastJSON,
  type EvolutionContrastRecord,
} from "@/lib/ai/evolutionContrastPolish";
import { buildCompareNarrativeGraph } from "@/lib/evolution/narrativeGraph.js";
import {
  polishCompareNarrativeGraphText,
  type CompareNarrativeGraphPayload,
} from "@/lib/ai/evolutionNarrativeGraphPolish";
import CompareSelector from "@/components/compare-selector";
import { pool } from "@/lib/db";
import { getComparePairFromDB, getCountryCardsFromDB } from "@/lib/db/queries";
import {
  getEventsWithCausalForCountry,
  getCachedInsight,
  upsertInsightCache,
} from "@/lib/db/evolutionQueries";
import { buildCountryEvolutionContrast } from "@/lib/evolution/contrastEngine.js";

export const dynamic = "force-dynamic";

export default async function ComparePage({
  params,
}: {
  params: Promise<{ pair: string }>;
}) {
  const { pair } = await params;
  const dash = pair.indexOf("-");
  if (dash < 0) {
    return <div className="p-10">Invalid pair</div>;
  }
  const leftCode = pair.slice(0, dash);
  const rightCode = pair.slice(dash + 1);

  const { left, right } = await getComparePairFromDB(leftCode, rightCode);

  if (!left || !right) {
    return <div className="p-10">Comparison not found</div>;
  }

  const allForSelector = await getCountryCardsFromDB();

  const selectorCountriesList = allForSelector.map((c) => ({
    code: c.code,
    name: c.name,
  }));

  const lp = Number(left.score.power_score ?? 0);
  const rp = Number(right.score.power_score ?? 0);
  const powerDiff = Math.round((lp - rp) * 100) / 100;
  const powerLeader =
    powerDiff > 0 ? left.name : powerDiff < 0 ? right.name : "Tie";

  const metrics = [
    {
      label: "Governance",
      leftValue: left.score.governance ?? 0,
      rightValue: right.score.governance ?? 0,
    },
    {
      label: "Social Order",
      leftValue: left.score.social_order ?? 0,
      rightValue: right.score.social_order ?? 0,
    },
    {
      label: "Economy",
      leftValue: left.score.economy ?? 0,
      rightValue: right.score.economy ?? 0,
    },
    {
      label: "Human Capital",
      leftValue: left.score.human_capital ?? 0,
      rightValue: right.score.human_capital ?? 0,
    },
    {
      label: "Infrastructure",
      leftValue: left.score.infrastructure ?? 0,
      rightValue: right.score.infrastructure ?? 0,
    },
    {
      label: "Innovation",
      leftValue: left.score.innovation ?? 0,
      rightValue: right.score.innovation ?? 0,
    },
    {
      label: "Openness",
      leftValue: left.score.openness ?? 0,
      rightValue: right.score.openness ?? 0,
    },
    {
      label: "Future Potential",
      leftValue: left.score.future_potential ?? 0,
      rightValue: right.score.future_potential ?? 0,
    },
  ];

  const insight = await generateAIInsight(
    { ...left, ...left.score },
    { ...right, ...right.score }
  );

  const leftCausal = await getEventsWithCausalForCountry(leftCode, 6);
  const rightCausal = await getEventsWithCausalForCountry(rightCode, 6);

  const compareCausalKey = `compare-causal:${[leftCode, rightCode].sort().join("-")}`;
  let compareCausal = (await getCachedInsight(compareCausalKey)) as {
    divergence_summary?: string;
    left_driver_events?: string[];
    right_driver_events?: string[];
    structural_thesis?: string;
  } | null;

  if (!compareCausal && process.env.OPENAI_API_KEY) {
    const facts = JSON.stringify({ left: leftCausal, right: rightCausal }).slice(
      0,
      12000
    );
    compareCausal = await generateCompareCausalSummaryAI(
      left.name,
      right.name,
      facts
    );
    try {
      await upsertInsightCache(
        compareCausalKey,
        compareCausal as Record<string, unknown>
      );
    } catch {
      /* optional cache */
    }
  }

  const ruleContrast = await buildCountryEvolutionContrast(pool, leftCode, rightCode);
  const contrastCacheKey = `evolution-contrast:${leftCode}-${rightCode}`;
  let evolutionContrast: EvolutionContrastRecord = {
    summary: ruleContrast.summary,
    direction_gap: ruleContrast.direction_gap,
    divergence_points: ruleContrast.divergence_points,
    left_drivers: ruleContrast.left_drivers,
    right_drivers: ruleContrast.right_drivers,
    stability_comparison: ruleContrast.stability_comparison,
    bottom_line: ruleContrast.bottom_line,
  };
  const cachedContrast = await getCachedInsight(contrastCacheKey);
  if (
    cachedContrast &&
    typeof cachedContrast.summary === "string" &&
    Array.isArray(cachedContrast.divergence_points)
  ) {
    evolutionContrast = {
      ...(cachedContrast as unknown as EvolutionContrastRecord),
      direction_gap: ruleContrast.direction_gap,
      stability_comparison: ruleContrast.stability_comparison,
      bottom_line: ruleContrast.bottom_line,
    };
  } else if (process.env.OPENAI_API_KEY) {
    try {
      const polished = await polishEvolutionContrastJSON(evolutionContrast);
      evolutionContrast = {
        ...polished,
        direction_gap: ruleContrast.direction_gap,
        stability_comparison: ruleContrast.stability_comparison,
        bottom_line: ruleContrast.bottom_line,
      };
      await upsertInsightCache(
        contrastCacheKey,
        evolutionContrast as Record<string, unknown>
      );
    } catch {
      evolutionContrast = {
        summary: ruleContrast.summary,
        direction_gap: ruleContrast.direction_gap,
        divergence_points: ruleContrast.divergence_points,
        left_drivers: ruleContrast.left_drivers,
        right_drivers: ruleContrast.right_drivers,
        stability_comparison: ruleContrast.stability_comparison,
        bottom_line: ruleContrast.bottom_line,
      };
    }
  }

  const ruleCompareGraph = (await buildCompareNarrativeGraph(
    pool,
    leftCode,
    rightCode
  )) as CompareNarrativeGraphPayload;
  const compareNarrativeGraphKey = `narrative-graph-compare:${leftCode}-${rightCode}`;
  const cachedCompareGraph = await getCachedInsight(compareNarrativeGraphKey);

  function mergeCompareKeyPaths(
    rulePaths: CompareNarrativeGraphPayload["key_paths"],
    cached: unknown
  ): CompareNarrativeGraphPayload["key_paths"] {
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

  let compareGraphDisplay: {
    summary: string;
    divergence_summary: string;
    left_arc: string;
    right_arc: string;
    key_paths: CompareNarrativeGraphPayload["key_paths"];
  } = {
    summary: ruleCompareGraph.summary,
    divergence_summary: ruleCompareGraph.divergence_summary,
    left_arc: ruleCompareGraph.left_arc,
    right_arc: ruleCompareGraph.right_arc,
    key_paths: ruleCompareGraph.key_paths,
  };

  if (
    cachedCompareGraph &&
    typeof cachedCompareGraph.divergence_summary === "string" &&
    typeof cachedCompareGraph.left_arc === "string" &&
    typeof cachedCompareGraph.right_arc === "string" &&
    typeof cachedCompareGraph.summary === "string" &&
    Array.isArray(cachedCompareGraph.key_paths)
  ) {
    compareGraphDisplay = {
      summary: cachedCompareGraph.summary as string,
      divergence_summary: cachedCompareGraph.divergence_summary as string,
      left_arc: cachedCompareGraph.left_arc as string,
      right_arc: cachedCompareGraph.right_arc as string,
      key_paths: mergeCompareKeyPaths(
        ruleCompareGraph.key_paths,
        cachedCompareGraph.key_paths
      ),
    };
  } else if (process.env.OPENAI_API_KEY && ruleCompareGraph.nodes.length > 0) {
    try {
      const polished = await polishCompareNarrativeGraphText(ruleCompareGraph);
      compareGraphDisplay = {
        summary: polished.summary,
        divergence_summary: polished.divergence_summary,
        left_arc: polished.left_arc,
        right_arc: polished.right_arc,
        key_paths: polished.key_paths,
      };
      await upsertInsightCache(compareNarrativeGraphKey, {
        summary: polished.summary,
        divergence_summary: polished.divergence_summary,
        left_arc: polished.left_arc,
        right_arc: polished.right_arc,
        key_paths: polished.key_paths,
      });
    } catch {
      compareGraphDisplay = {
        summary: ruleCompareGraph.summary,
        divergence_summary: ruleCompareGraph.divergence_summary,
        left_arc: ruleCompareGraph.left_arc,
        right_arc: ruleCompareGraph.right_arc,
        key_paths: ruleCompareGraph.key_paths,
      };
    }
  }

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-6xl">
        <h1 className="text-4xl font-bold">
          {left.name} vs {right.name}
        </h1>

        <CompareSelector
          countries={selectorCountriesList}
          defaultLeft={left.code}
          defaultRight={right.code}
        />

        <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-2">
          <div className="rounded-xl border p-6">
            <h2 className="text-2xl font-semibold">{left.name}</h2>
            <p className="mt-2 text-gray-500">{left.region}</p>
            <div className="mt-4 space-y-2 text-sm">
              <p>GDP: ${left.gdp.toLocaleString()}</p>
              <p>Population: {left.population.toLocaleString()}</p>
              <p>Overall: {left.score.overall}</p>
              <p className="font-semibold">
                Power: {left.score.power_score ?? "—"}{" "}
                <span className="font-normal text-gray-500">
                  ({left.score.power_classification ?? "—"})
                </span>
              </p>
              <p>Risk: {left.score.risk}</p>
              <p>Opportunity: {left.score.opportunity}</p>
            </div>
          </div>

          <div className="rounded-xl border p-6">
            <h2 className="text-2xl font-semibold">{right.name}</h2>
            <p className="mt-2 text-gray-500">{right.region}</p>
            <div className="mt-4 space-y-2 text-sm">
              <p>GDP: ${right.gdp.toLocaleString()}</p>
              <p>Population: {right.population.toLocaleString()}</p>
              <p>Overall: {right.score.overall}</p>
              <p className="font-semibold">
                Power: {right.score.power_score ?? "—"}{" "}
                <span className="font-normal text-gray-500">
                  ({right.score.power_classification ?? "—"})
                </span>
              </p>
              <p>Risk: {right.score.risk}</p>
              <p>Opportunity: {right.score.opportunity}</p>
            </div>
          </div>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 bg-gray-50 p-6">
          <h2 className="text-xl font-semibold">Power comparison</h2>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <div>
              <p className="text-xs uppercase text-gray-500">{left.name}</p>
              <p className="text-2xl font-bold">{left.score.power_score ?? "—"}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-gray-500">Δ (left − right)</p>
              <p className="text-2xl font-bold">
                {left.score.power_score != null && right.score.power_score != null
                  ? powerDiff
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase text-gray-500">{right.name}</p>
              <p className="text-2xl font-bold">{right.score.power_score ?? "—"}</p>
            </div>
          </div>
          <p className="mt-4 text-sm text-gray-700">
            <span className="font-medium">Power edge:</span> {powerLeader}
            {powerDiff !== 0 && left.score.power_score != null && right.score.power_score != null
              ? ` (Δ ${powerDiff > 0 ? "+" : ""}${powerDiff})`
              : ""}
          </p>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Causal comparison</h2>
          <p className="mt-1 text-sm text-gray-500">
            Recent events with structured consequences — who moved which dimensions.
          </p>
          <div className="mt-6 grid gap-8 md:grid-cols-2">
            <div>
              <h3 className="font-semibold">{left.name}</h3>
              <ul className="mt-3 space-y-4 text-sm">
                {leftCausal.map((e) => (
                  <li key={e.id} className="rounded-lg border border-gray-100 p-3">
                    <p className="font-medium">{e.title}</p>
                    <p className="text-gray-600">
                      {e.consequences.slice(0, 3).map((c) => (
                        <span key={c.dimension} className="mr-2 inline-block">
                          {c.dimension}{" "}
                          {c.impact_value > 0 ? "+" : ""}
                          {c.impact_value}
                        </span>
                      ))}
                      {e.consequences.length === 0 && (
                        <span className="text-gray-400">No consequence rows</span>
                      )}
                    </p>
                    {e.actors.length > 0 && (
                      <p className="mt-1 text-xs text-gray-500">
                        {e.actors.map((a) => `${a.name} (${a.role})`).join(" · ")}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">{right.name}</h3>
              <ul className="mt-3 space-y-4 text-sm">
                {rightCausal.map((e) => (
                  <li key={e.id} className="rounded-lg border border-gray-100 p-3">
                    <p className="font-medium">{e.title}</p>
                    <p className="text-gray-600">
                      {e.consequences.slice(0, 3).map((c) => (
                        <span key={c.dimension} className="mr-2 inline-block">
                          {c.dimension}{" "}
                          {c.impact_value > 0 ? "+" : ""}
                          {c.impact_value}
                        </span>
                      ))}
                      {e.consequences.length === 0 && (
                        <span className="text-gray-400">No consequence rows</span>
                      )}
                    </p>
                    {e.actors.length > 0 && (
                      <p className="mt-1 text-xs text-gray-500">
                        {e.actors.map((a) => `${a.name} (${a.role})`).join(" · ")}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {compareCausal && (
            <div className="mt-8 rounded-xl bg-slate-50 p-5">
              <h3 className="font-semibold">Compare causal summary (AI)</h3>
              <p className="mt-2 text-sm text-gray-800">
                {compareCausal.divergence_summary}
              </p>
              <p className="mt-3 text-sm text-gray-700">
                {compareCausal.structural_thesis}
              </p>
              <div className="mt-4 grid gap-4 md:grid-cols-2 text-sm">
                <div>
                  <p className="font-medium text-gray-600">{left.name} drivers</p>
                  <ul className="mt-2 list-disc pl-5">
                    {(compareCausal.left_driver_events || []).map((x, i) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="font-medium text-gray-600">{right.name} drivers</p>
                  <ul className="mt-2 list-disc pl-5">
                    {(compareCausal.right_driver_events || []).map((x, i) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Dimension Differences</h2>

          <div className="mt-6 space-y-5">
            {metrics.map((metric) => {
              const diff = metric.leftValue - metric.rightValue;
              const winner =
                diff > 0 ? left.name : diff < 0 ? right.name : "Tie";
              const diffAbs = Math.abs(diff);

              return (
                <div key={metric.label} className="rounded-xl border border-gray-100 p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-medium">{metric.label}</span>

                    {diff !== 0 ? (
                      <span className="text-sm font-semibold text-gray-700">
                        {winner} +{diffAbs}
                      </span>
                    ) : (
                      <span className="text-sm text-gray-400">Tie</span>
                    )}
                  </div>

                  <div className="space-y-3">
                    <div>
                      <div className="mb-1 flex items-center justify-between text-sm">
                        <span className="font-medium">{left.name}</span>
                        <span>{metric.leftValue}</span>
                      </div>
                      <div className="h-3 w-full rounded-full bg-gray-100">
                        <div
                          className="h-3 rounded-full bg-black"
                          style={{
                            width: `${metric.leftValue}%`,
                            opacity: diff >= 0 ? 1 : 0.35,
                          }}
                        />
                      </div>
                    </div>

                    <div>
                      <div className="mb-1 flex items-center justify-between text-sm">
                        <span className="font-medium">{right.name}</span>
                        <span>{metric.rightValue}</span>
                      </div>
                      <div className="h-3 w-full rounded-full bg-gray-100">
                        <div
                          className="h-3 rounded-full bg-gray-400"
                          style={{
                            width: `${metric.rightValue}%`,
                            opacity: diff <= 0 ? 1 : 0.45,
                          }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">AI Decision</h2>

          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <p className="text-sm text-gray-500">Winner</p>
            <p className="mt-1 text-2xl font-bold">
              {insight.winner || "Unknown"}
            </p>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Power perspective</h3>
            <p className="mt-2 text-gray-700">
              {insight.power_delta_summary || "No power summary generated."}
            </p>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Core advantages</h3>
            <ul className="mt-2 list-disc space-y-2 pl-5 text-gray-700">
              {(insight.core_advantages || []).map((item: string, index: number) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Core risks</h3>
            <ul className="mt-2 list-disc space-y-2 pl-5 text-gray-700">
              {(insight.core_risks || []).map((item: string, index: number) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>

          <div className="mt-6">
            <h3 className="font-semibold">Bottom line</h3>
            <p className="mt-2 text-gray-700">
              {insight.bottom_line || "No takeaway generated."}
            </p>
          </div>
        </div>

        <section className="mt-12 rounded-2xl border border-gray-200 p-6">
          <h2 className="text-xl font-semibold">Evolution Contrast</h2>
          <p className="mt-1 text-sm text-gray-500">
            Rule engine: paired snapshot windows, trendEngine, event_consequences, and chains.
            Optional wording may be cached under <code className="text-xs">{contrastCacheKey}</code>
            ; direction gap / stability / bottom line anchored to recomputed rules.
          </p>
          <p className="mt-4 text-gray-800">{evolutionContrast.summary}</p>
          <div className="mt-6 rounded-xl bg-slate-50 p-4 text-sm">
            <p className="text-xs font-semibold uppercase text-gray-500">Direction gap</p>
            <p className="mt-2 text-gray-900">{evolutionContrast.direction_gap}</p>
          </div>
          <div className="mt-6">
            <h3 className="font-semibold">Divergence points</h3>
            <ul className="mt-3 list-disc space-y-3 pl-5 text-sm text-gray-700">
              {(evolutionContrast.divergence_points || []).map((dp, i) => (
                <li key={i}>
                  <span className="font-medium">{dp.event}</span>
                  <p className="text-gray-600">{dp.impact}</p>
                  <p className="mt-1 text-gray-500">{dp.why_it_mattered}</p>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <div>
              <h3 className="font-semibold">{left.name} — drivers</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
                {(evolutionContrast.left_drivers || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">{right.name} — drivers</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-gray-700">
                {(evolutionContrast.right_drivers || []).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </div>
          </div>
          <div className="mt-6 rounded-xl border border-gray-100 p-4 text-sm text-gray-800">
            <p className="text-xs font-semibold uppercase text-gray-500">
              Stability comparison
            </p>
            <p className="mt-2">{evolutionContrast.stability_comparison}</p>
          </div>
          <div className="mt-6">
            <h3 className="font-semibold">Bottom line</h3>
            <p className="mt-2 text-gray-800">{evolutionContrast.bottom_line}</p>
          </div>
        </section>

        <section className="mt-12 rounded-2xl border border-dashed border-gray-200 p-6">
          <h2 className="text-lg font-semibold">Narrative Graph Summary</h2>
          <p className="mt-1 text-xs text-gray-500">
            Paired rule graph from PostgreSQL; text may be cached under{" "}
            <code className="text-xs">{compareNarrativeGraphKey}</code>.
          </p>
          <p className="mt-4 text-sm text-gray-800">{compareGraphDisplay.divergence_summary}</p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <p className="text-xs font-semibold uppercase text-gray-500">Left arc</p>
              <p className="mt-1 text-sm text-gray-800">{compareGraphDisplay.left_arc}</p>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase text-gray-500">Right arc</p>
              <p className="mt-1 text-sm text-gray-800">{compareGraphDisplay.right_arc}</p>
            </div>
          </div>
          <div className="mt-4">
            <h3 className="text-sm font-semibold text-gray-700">Key paths</h3>
            <ul className="mt-2 list-decimal space-y-2 pl-5 text-sm text-gray-700">
              {compareGraphDisplay.key_paths.slice(0, 3).map((kp, i) => (
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
