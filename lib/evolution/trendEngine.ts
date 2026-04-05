/**
 * Types for trend engine (runtime: trendEngine.js).
 */

export type TrendResult = {
  label: string;
  detail: string;
};

export type FederalTrendBundle = {
  power_trend_extended: string;
  conflict_trend: string;
  influence_trend: string;
};
