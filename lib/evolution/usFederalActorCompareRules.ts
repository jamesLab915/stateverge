/**
 * Deterministic pairwise comparison of two federal actors (not AI).
 */

import type { FederalActorRow } from "@/lib/db/usFederalQueries";

export type FederalActorCompareRulesOutput = {
  winner: string;
  difference_summary: string;
  core_advantages: string[];
  core_risks: string[];
  role_contrast: string;
  bottom_line: string;
};

function n(v: number | null | undefined): number {
  if (v == null || Number.isNaN(Number(v))) return 0;
  return Number(v);
}

export function buildFederalActorCompareRules(input: {
  left: FederalActorRow;
  right: FederalActorRow;
  leftRecentEventCount: number;
  rightRecentEventCount: number;
}): FederalActorCompareRulesOutput {
  const L = input.left;
  const R = input.right;
  const lf = n(L.formal_power_score);
  const rf = n(R.formal_power_score);
  const li = n(L.political_influence_score);
  const ri = n(R.political_influence_score);
  const lc = n(L.conflict_index);
  const rc = n(R.conflict_index);

  const formalLeader = lf > rf ? L.name : rf > lf ? R.name : null;
  const inflLeader = li > ri ? L.name : ri > li ? R.name : null;

  let winner: string;
  if (formalLeader && lf - rf >= 8) {
    winner = formalLeader;
  } else if (inflLeader && Math.abs(li - ri) >= 10) {
    winner = inflLeader;
  } else if (formalLeader && inflLeader && formalLeader === inflLeader) {
    winner = formalLeader;
  } else {
    winner = `Structurally split: ${L.name} vs ${R.name}`;
  }

  const diffBits: string[] = [];
  diffBits.push(
    `Formal power spread: ${L.name} ${lf} vs ${R.name} ${rf} (truth layer from positions).`
  );
  diffBits.push(`Political influence spread: ${li} vs ${ri} (event-centrality model).`);
  diffBits.push(`Conflict exposure index: ${lc} vs ${rc}.`);
  diffBits.push(
    `Recent federal event ties (window): ${input.leftRecentEventCount} vs ${input.rightRecentEventCount}.`
  );

  const core_advantages: string[] = [];
  const core_risks: string[] = [];

  if (lf >= rf + 5) {
    core_advantages.push(`${L.name} holds higher formal authority under current position weights.`);
  } else if (rf >= lf + 5) {
    core_advantages.push(`${R.name} holds higher formal authority under current position weights.`);
  } else {
    core_advantages.push("Formal power is close — outcomes hinge on statute, jurisdiction, and coalition politics.");
  }

  if (li >= ri + 8) {
    core_advantages.push(`${L.name} shows higher modelled influence from recent event centrality.`);
  } else if (ri >= li + 8) {
    core_advantages.push(`${R.name} shows higher modelled influence from recent event centrality.`);
  }

  if (lc >= rc + 6) {
    core_risks.push(`${L.name} carries higher conflict_index from opposing/removal/harm signals in events.`);
  } else if (rc >= lc + 6) {
    core_risks.push(`${R.name} carries higher conflict_index from opposing/removal/harm signals in events.`);
  } else {
    core_risks.push("Conflict exposure is in a similar band — neither profile is clearly 'quiet' on this metric.");
  }

  if (input.leftRecentEventCount > input.rightRecentEventCount + 2) {
    core_advantages.push(`${L.name} has more recent documented federal event involvement in the window.`);
  } else if (input.rightRecentEventCount > input.leftRecentEventCount + 2) {
    core_advantages.push(`${R.name} has more recent documented federal event involvement in the window.`);
  }

  const role_contrast = `${L.name} (${L.actor_type}, ${L.power_status ?? "status n/a"}) vs ${R.name} (${R.actor_type}, ${R.power_status ?? "status n/a"}): ${L.branch ?? "?"} branch / ${L.office_title ?? "—"} versus ${R.branch ?? "?"} / ${R.office_title ?? "—"}.`;

  const bottom_line =
    formalLeader && formalLeader === inflLeader
      ? `${formalLeader} leads on both formal power and modelled influence in this snapshot.`
      : formalLeader && inflLeader && formalLeader !== inflLeader
        ? `Formal anchor: ${formalLeader}; influence leader: ${inflLeader} — structure and momentum diverge.`
        : "No single dominant profile: compare formal vs influence vs conflict exposure explicitly for decisions.";

  return {
    winner,
    difference_summary: diffBits.join(" "),
    core_advantages: core_advantages.slice(0, 5),
    core_risks: core_risks.slice(0, 5),
    role_contrast,
    bottom_line,
  };
}

/** Stable cache key for an unordered pair of slugs (slugs may contain hyphens). */
export function federalActorCompareCacheKey(leftSlug: string, rightSlug: string): string {
  const [a, b] = [leftSlug, rightSlug].sort((x, y) => x.localeCompare(y));
  return `actor-compare:${a}--${b}`;
}
