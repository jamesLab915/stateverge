/**
 * Deterministic US federal power scenario rules (not AI).
 * Outputs three fixed branches: bullish / neutral / bearish.
 */

export type FederalScenarioBranch = {
  title: string;
  conditions: string[];
  impact: string;
  key_actors: string[];
  confidence: number;
};

export type FederalScenarioRulesOutput = {
  bullish: FederalScenarioBranch;
  neutral: FederalScenarioBranch;
  bearish: FederalScenarioBranch;
  overall_judgment: string;
  consolidation_index: number;
};

export type ScenarioSnapshotFields = {
  overall_power_stability: number | null;
  executive_cohesion: number | null;
  cabinet_stability: number | null;
  legislative_alignment: number | null;
  conflict_temperature: number | null;
  narrative_pressure: number | null;
};

export type ScenarioEventLite = {
  event_type: string;
  impact_direction: string | null;
  confidence: string;
};

export type ScenarioActorLite = {
  name: string;
  slug: string;
  conflict_index: number | null;
  political_influence_score: number | null;
  formal_power_score: number | null;
};

const BEARISH_EVENT_TYPES = new Set([
  "dismissal",
  "resignation",
  "conflict",
  "scandal",
  "investigation",
  "budget_fight",
]);

const BULLISH_EVENT_TYPES = new Set([
  "confirmation",
  "appointment",
  "nomination",
]);

function n(v: number | null | undefined, fallback = 50): number {
  if (v == null || Number.isNaN(Number(v))) return fallback;
  return Math.max(0, Math.min(100, Number(v)));
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, x));
}

function roundScore(x: number): number {
  return Math.round(clamp(x, 28, 96));
}

export function buildFederalScenarioRules(input: {
  snapshot: ScenarioSnapshotFields | null;
  recentEvents: ScenarioEventLite[];
  topByInfluence: ScenarioActorLite[];
  topByConflict: ScenarioActorLite[];
}): FederalScenarioRulesOutput {
  const s = input.snapshot;
  const stab = n(s?.overall_power_stability);
  const exec = n(s?.executive_cohesion);
  const cab = n(s?.cabinet_stability);
  const leg = n(s?.legislative_alignment);
  const conflict = n(s?.conflict_temperature);
  const narr = n(s?.narrative_pressure);

  let consSignal = 0;
  let fragSignal = 0;
  for (const e of input.recentEvents.slice(0, 20)) {
    const t = (e.event_type || "").toLowerCase();
    if (BEARISH_EVENT_TYPES.has(t)) fragSignal += 4;
    if (BULLISH_EVENT_TYPES.has(t)) consSignal += 3;
    const dir = (e.impact_direction || "").toLowerCase();
    if (dir === "negative") fragSignal += 2;
    if (dir === "positive") consSignal += 2;
    if ((e.confidence || "").toLowerCase() === "speculative") fragSignal += 0.5;
  }

  const cohesionBlend = (exec + cab) / 2;
  let consolidationIndex =
    0.22 * stab +
    0.2 * cohesionBlend +
    0.18 * leg +
    0.14 * (100 - conflict) +
    0.1 * (100 - narr) +
    0.08 * exec +
    0.08 * cab;

  consolidationIndex += (consSignal - fragSignal) * 0.35;
  consolidationIndex = clamp(consolidationIndex, 0, 100);

  const infl = [...input.topByInfluence].slice(0, 5);
  const hot = [...input.topByConflict].slice(0, 5);
  const namesInfl = infl.map((a) => a.name);
  const namesHot = hot.map((a) => a.name);

  const bullActors = namesInfl.slice(0, 4);
  const bearActors = [...new Set([...namesHot.slice(0, 3), ...namesInfl.slice(0, 2)])].slice(
    0,
    4
  );
  const neutActors = namesInfl.slice(0, 3);

  const bullCond: string[] = [];
  if (stab >= 58) bullCond.push("Overall power stability index is elevated vs. stress baseline.");
  if (cohesionBlend >= 58)
    bullCond.push("Executive and cabinet cohesion readings support aligned implementation.");
  if (leg >= 52) bullCond.push("Legislative alignment is not strongly negative in the latest snapshot.");
  if (conflict <= 52) bullCond.push("Measured conflict temperature is not in the top stress band.");
  if (consSignal > fragSignal + 4)
    bullCond.push("Recent event mix leans toward confirmations/appointments over removals/conflict.");
  if (bullCond.length === 0)
    bullCond.push("Composite consolidation index sits above midline in the rules engine.");

  const bearCond: string[] = [];
  if (stab <= 48) bearCond.push("Overall stability index is soft — more room for disruptive shocks.");
  if (conflict >= 56) bearCond.push("Conflict temperature is elevated in the latest snapshot.");
  if (narr >= 56) bearCond.push("Narrative pressure is high — agenda fights may spill into institutions.");
  if (leg <= 45) bearCond.push("Legislative alignment is weak — friction on budgets and confirmations.");
  if (fragSignal > consSignal + 4)
    bearCond.push("Recent events skew toward dismissals, investigations, or open conflict signals.");
  if (bearCond.length === 0)
    bearCond.push("Bearish path is a structural alternative if cohesion or stability deteriorates.");

  const neutCond: string[] = [
    `Consolidation index is near ${Math.round(consolidationIndex)} — neither extreme dominates.`,
    "Executive formal hierarchy remains while political contestation continues on key issues.",
  ];
  if (Math.abs(stab - conflict) < 12) neutCond.push("Stability and conflict signals partially offset each other.");

  const bullConf = roundScore(50 + (consolidationIndex - 50) * 0.85 + (consSignal - fragSignal) * 0.4);
  const bearConf = roundScore(
    50 + (50 - consolidationIndex) * 0.85 + (fragSignal - consSignal) * 0.4
  );
  const neutConf = roundScore(46 + (22 - Math.abs(consolidationIndex - 50)) * 1.15);

  let overall_judgment: string;
  if (consolidationIndex >= 62) {
    overall_judgment =
      "Measured indicators currently skew toward consolidation: stability and cohesion are relatively stronger than conflict/narrative stress, subject to new high-impact events.";
  } else if (consolidationIndex <= 42) {
    overall_judgment =
      "Indicators skew toward fragmentation or instability: conflict, narrative pressure, or weak alignment weigh on the composite, even if formal hierarchy remains.";
  } else {
    overall_judgment =
      "The federal power structure looks like contested equilibrium: formal authority is stable while political conflict and narrative pressure shape day-to-day outcomes.";
  }

  const bullish: FederalScenarioBranch = {
    title: "Consolidation / bullish executive path",
    conditions: bullCond.slice(0, 5),
    impact:
      "Policy implementation and staffing continuity improve; fewer forced reversals; coalition management costs decline modestly if the trajectory holds.",
    key_actors: bullActors.length ? bullActors : ["—"],
    confidence: bullConf,
  };

  const neutral: FederalScenarioBranch = {
    title: "Contested equilibrium",
    conditions: neutCond.slice(0, 5),
    impact:
      "Major decisions proceed but face friction; mixed messaging; selective enforcement and bargaining across branches and agencies.",
    key_actors: neutActors.length ? neutActors : ["—"],
    confidence: neutConf,
  };

  const bearish: FederalScenarioBranch = {
    title: "Fragmentation / elevated instability",
    conditions: bearCond.slice(0, 5),
    impact:
      "Higher risk of leadership churn, public splits, and stalled initiatives; departments may hedge or slow-roll contested directives.",
    key_actors: bearActors.length ? bearActors : ["—"],
    confidence: bearConf,
  };

  return {
    bullish,
    neutral,
    bearish,
    overall_judgment,
    consolidation_index: Math.round(consolidationIndex * 10) / 10,
  };
}
