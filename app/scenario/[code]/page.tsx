import Link from "next/link";
import { pool } from "@/lib/db";
import { buildRuleScenarioBundle } from "@/lib/evolution/ruleScenarios";
import { getCachedInsight } from "@/lib/db/evolutionQueries";
import type { CountryScoreBundle } from "@/lib/db/queries";

export const dynamic = "force-dynamic";

type ScenarioLeg = {
  title?: string;
  conditions?: string[];
  impact?: string;
};

function LegCard({
  label,
  leg,
  accent,
}: {
  label: string;
  leg: ScenarioLeg;
  accent: string;
}) {
  return (
    <div className={`rounded-2xl border p-6 ${accent}`}>
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">{label}</p>
      <h3 className="mt-2 text-xl font-bold">{leg.title || "—"}</h3>
      <ul className="mt-4 list-disc space-y-2 pl-5 text-sm text-gray-700">
        {(leg.conditions || []).map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
      <p className="mt-4 text-sm leading-relaxed text-gray-800">{leg.impact || "—"}</p>
      <p className="mt-3 text-xs text-gray-500">
        Dimensions: structural read — not a forecast guarantee.
      </p>
    </div>
  );
}

export default async function ScenarioPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  const res = await pool.query(
    `
    SELECT c.name, c.code,
      s.governance, s.social_order, s.economy, s.human_capital,
      s.infrastructure, s.innovation, s.openness, s.future_potential,
      s.overall, s.risk, s.opportunity, s.power_score, s.power_classification,
      (SELECT power_trend FROM country_score_snapshots z
       WHERE z.country_code = c.code ORDER BY z.id DESC LIMIT 1) AS power_trend
    FROM countries c
    JOIN country_scores s ON s.country_code = c.code
    WHERE c.code = $1
    `,
    [code]
  );

  if (res.rows.length === 0) {
    return <div className="p-10">Country not found</div>;
  }

  const row = res.rows[0];
  const score: CountryScoreBundle = {
    governance: Number(row.governance),
    social_order: Number(row.social_order),
    economy: Number(row.economy),
    human_capital: Number(row.human_capital),
    infrastructure: Number(row.infrastructure),
    innovation: Number(row.innovation),
    openness: Number(row.openness),
    future_potential: Number(row.future_potential),
    overall: Number(row.overall),
    risk: Number(row.risk),
    opportunity: Number(row.opportunity),
    power_score: row.power_score != null ? Number(row.power_score) : null,
    power_classification:
      row.power_classification != null ? String(row.power_classification) : null,
  };

  const { rows: evRows } = await pool.query(
    `SELECT title, impact_direction FROM events WHERE country_code = $1 ORDER BY id DESC LIMIT 8`,
    [code]
  );

  const rules = buildRuleScenarioBundle(
    score,
    row.power_trend != null ? String(row.power_trend) : null,
    evRows as { title: string; impact_direction: string }[]
  );

  const aiRaw = await getCachedInsight(`scenario:${code}`);
  const ai = aiRaw as
    | {
        bullish?: ScenarioLeg;
        neutral?: ScenarioLeg;
        bearish?: ScenarioLeg;
      }
    | null;

  return (
    <main className="min-h-screen bg-white px-6 py-10 text-gray-900">
      <div className="mx-auto max-w-5xl">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-gray-500">
          Scenario engine · rules v1
        </p>
        <h1 className="mt-2 text-3xl font-bold">{row.name}: future paths</h1>
        <p className="mt-2 max-w-2xl text-gray-600">
          Three bracketed trajectories from current structure, power trend, risk/opportunity,
          and recent event skew. Run <code className="text-xs">npm run evolve</code> or{" "}
          <code className="text-xs">node scripts/generateScenarios.js</code> to refresh the narrative
          cache layer.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link href={`/countries/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Profile
          </Link>
          <Link href={`/timeline/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Timeline
          </Link>
          <Link href={`/causal/${code}`} className="rounded-xl border px-4 py-2 text-sm">
            Causal
          </Link>
        </div>

        <section className="mt-12">
          <h2 className="text-lg font-semibold">Rule-based scenarios</h2>
          <p className="text-sm text-gray-500">Source: {rules.source}</p>
          <div className="mt-6 grid gap-6 md:grid-cols-3">
            <LegCard label="Bullish" leg={rules.bullish} accent="bg-emerald-50/80 border-emerald-100" />
            <LegCard label="Neutral" leg={rules.neutral} accent="bg-gray-50 border-gray-200" />
            <LegCard label="Bearish" leg={rules.bearish} accent="bg-rose-50/80 border-rose-100" />
          </div>
        </section>

        {ai && (ai.bullish || ai.neutral || ai.bearish) && (
          <section className="mt-14">
            <h2 className="text-lg font-semibold">Narrative layer (cached AI)</h2>
            <p className="text-sm text-gray-500">
              Structured JSON from last <code className="text-xs">generateScenarios.js</code> run.
            </p>
            <div className="mt-6 grid gap-6 md:grid-cols-3">
              {ai.bullish && (
                <LegCard label="Bullish (AI)" leg={ai.bullish} accent="border border-gray-200" />
              )}
              {ai.neutral && (
                <LegCard label="Neutral (AI)" leg={ai.neutral} accent="border border-gray-200" />
              )}
              {ai.bearish && (
                <LegCard label="Bearish (AI)" leg={ai.bearish} accent="border border-gray-200" />
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
