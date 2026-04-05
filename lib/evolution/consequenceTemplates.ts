/**
 * Types for consequence templates (runtime: consequenceTemplates.js).
 */

export type TemplateImpact = {
  dimension: string;
  value: number;
  horizon: string;
};

export type ConsequenceTemplate = {
  impacts: TemplateImpact[];
  stability_effect: number;
  conflict_effect: number;
  explanation: string;
};

export type CountryConsequenceRow = {
  dimension: string;
  impact_value: number;
  time_horizon: string;
  confidence: number;
  explanation: string;
};

export type FederalConsequenceRow = {
  target_type: string;
  target_actor_id: number | null;
  target_department: string | null;
  dimension: string;
  impact_value: number;
  time_horizon: string;
  confidence: string;
  explanation: string;
};
