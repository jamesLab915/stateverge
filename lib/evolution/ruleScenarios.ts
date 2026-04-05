import type { CountryScoreBundle } from "@/lib/db/queries";
import type { EventWithCausal } from "@/lib/db/evolutionQueries";

export type ScenarioLeg = {
  title: string;
  conditions: string[];
  impact: string;
  dimension_focus: string[];
};

export type RuleScenarioBundle = {
  bullish: ScenarioLeg;
  neutral: ScenarioLeg;
  bearish: ScenarioLeg;
  source: "rules_v1";
};

export function buildRuleScenarioBundle(
  score: CountryScoreBundle,
  powerTrend: string | null,
  recentEvents: Pick<EventWithCausal, "title" | "impact_direction">[]
): RuleScenarioBundle {
  const risk = score.risk ?? 0;
  const opp = score.opportunity ?? 0;
  const fp = score.future_potential ?? 0;
  const trend = (powerTrend || "stable").toLowerCase();
  const evNeg = recentEvents.filter((e) => e.impact_direction === "negative").length;
  const evPos = recentEvents.filter((e) => e.impact_direction === "positive").length;

  const bullish: ScenarioLeg = {
    title: "Reform & upside path",
    conditions: [
      `Power trend ${trend} with opportunity ${opp} and future_potential ${fp}`,
      evPos >= evNeg
        ? "Recent shock balance skews constructive."
        : "Room for policy to stabilize sentiment if execution holds.",
    ],
    impact:
      "Overall and power drift up if governance holds: innovation and openness outperform, risk bleeds down slowly.",
    dimension_focus: ["future_potential", "innovation", "openness", "opportunity"],
  };

  const neutral: ScenarioLeg = {
    title: "Drift / muddle-through",
    conditions: [
      `Risk ${risk} sits mid-band; power trend ${trend}`,
      "No single vector dominates — outcomes path-dependent on external shocks.",
    ],
    impact:
      "Scores oscillate inside a band; power_score moves slowly unless a large event hits trade or institutions.",
    dimension_focus: ["governance", "economy", "risk"],
  };

  const bearish: ScenarioLeg = {
    title: "Conflict / stagnation pressure",
    conditions: [
      risk >= 55 ? `Elevated risk (${risk}) caps upside.` : "Risk not extreme but fragility latent.",
      evNeg > evPos
        ? "Latest events skew negative — contagion to confidence likely."
        : "If external conflict or sanctions intensify, downside opens fast.",
    ],
    impact:
      "Risk and social_order absorb hits first; openness and innovation stall; power_score compresses if economy cracks.",
    dimension_focus: ["risk", "social_order", "openness", "economy"],
  };

  return { bullish, neutral, bearish, source: "rules_v1" };
}
