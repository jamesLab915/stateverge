import type { EventWithCausal } from "@/lib/db/evolutionQueries";
import type { CausalSummaryAI } from "@/lib/ai/evolutionInsights";

export function buildRuleCausalSummary(
  countryName: string,
  events: EventWithCausal[],
  overall: number,
  power: number | null
): CausalSummaryAI {
  const major_causes: CausalSummaryAI["major_causes"] = [];

  for (const e of events) {
    if (e.consequences.length === 0) {
      major_causes.push({
        event: e.title,
        dimension: "aggregate",
        impact: `${e.impact_direction} strength ${e.impact_strength} (legacy mapping)`,
        why_it_matters:
          "Feeds governance/economy/innovation channels via event-type heuristics when no structured consequences exist.",
      });
      continue;
    }
    for (const c of e.consequences.slice(0, 2)) {
      major_causes.push({
        event: e.title,
        dimension: c.dimension,
        impact: `${c.impact_value > 0 ? "+" : ""}${c.impact_value} · ${c.time_horizon}`,
        why_it_matters:
          c.explanation ||
          "Shifts a state dimension that rolls into overall and power_score.",
      });
    }
  }

  const actorLines: string[] = [];
  for (const e of events) {
    for (const a of e.actors) {
      actorLines.push(`${a.name} (${a.actor_type}) — ${a.role} on «${e.title}»`);
    }
  }

  return {
    current_state_summary: `${countryName} is at overall ${overall} and power ${power ?? "n/a"}. Listed causes are derived from logged events and structured consequences (v1 rules).`,
    major_causes: major_causes.slice(0, 6),
    actor_summary:
      actorLines.length > 0
        ? [...new Set(actorLines)].slice(0, 6)
        : ["No actors linked to recent events yet — auto-events can attach primary_actor."],
  };
}
