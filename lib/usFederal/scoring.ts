/**
 * Rule-based federal actor classification. Not AI — interpretive layer only.
 */

export type PowerStatus =
  | "Central Actor"
  | "Rising Actor"
  | "Stable Actor"
  | "Contested Actor"
  | "Fragile Actor"
  | "Marginal Actor";

export function computePowerStatus(input: {
  formal_power_score: number;
  political_influence_score: number;
  conflict_index: number;
}): PowerStatus {
  const f = input.formal_power_score;
  const inf = input.political_influence_score;
  const c = input.conflict_index;

  if (f < 30) return "Marginal Actor";
  if (c >= 45) return "Contested Actor";
  if (f >= 95 && c < 18) return "Central Actor";
  if (f >= 90 && c >= 18) return "Contested Actor";
  if (inf > f + 8 && c < 35) return "Rising Actor";
  if (c >= 28 && f < 85 && f >= 45) return "Fragile Actor";
  if (f >= 85 && c < 25) return "Stable Actor";
  if (f >= 70) return "Stable Actor";
  return "Marginal Actor";
}

export function clampScore(v: number, lo = 0, hi = 100): number {
  if (!Number.isFinite(v)) return lo;
  return Math.round(Math.min(hi, Math.max(lo, v)) * 100) / 100;
}
