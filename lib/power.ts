import {
  POWER_WEIGHTS,
  computePowerScore as computePowerScoreRaw,
  classifyPower as classifyPowerRaw,
  powerTrendFromScores as powerTrendFromScoresRaw,
  buildPowerNote as buildPowerNoteRaw,
} from "./powerMath.js";

export { POWER_WEIGHTS };

export type PowerTrend = "rising" | "stable" | "declining";

export type PowerDimensions = {
  economy?: number | null;
  innovation?: number | null;
  infrastructure?: number | null;
  governance?: number | null;
  openness?: number | null;
  future_potential?: number | null;
  risk?: number | null;
};

export function computePowerScore(
  row: PowerDimensions & Record<string, unknown>
): number {
  return computePowerScoreRaw(row);
}

export function classifyPower(
  row: PowerDimensions & { power_score?: number | null } & Record<string, unknown>
): string {
  return classifyPowerRaw(row);
}

export function powerTrendFromScores(
  prevPower: number | null | undefined,
  nextPower: number | null | undefined
): { power_delta: number | null; power_trend: PowerTrend } {
  const r = powerTrendFromScoresRaw(prevPower, nextPower);
  return {
    power_delta: r.power_delta,
    power_trend: r.power_trend as PowerTrend,
  };
}

export function buildPowerNote(
  power_trend: string,
  power_delta: number | null | undefined
): string {
  return buildPowerNoteRaw(power_trend, power_delta);
}
