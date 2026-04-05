/**
 * Event Source Layer v1 — canonical source_type / confidence for country `events`
 * and US `federal_events`. Truth-layer scoring may optionally skip low-trust types (see updateScores).
 */

/** @readonly */
const CANONICAL_SOURCE_TYPES = [
  "official",
  "institutional",
  "reputable_media",
  "market_data",
  "x_signal",
  "inferred",
  "ai_generated",
];

/** Map legacy / informal labels → canonical slug. */
const SOURCE_TYPE_ALIASES = {
  departmental: "institutional",
  government: "official",
  gov: "official",
  agency: "institutional",
  congress: "institutional",
  congressional: "institutional",
  media: "reputable_media",
  press: "reputable_media",
  news: "reputable_media",
  market: "market_data",
  exchange: "market_data",
  social: "x_signal",
  twitter: "x_signal",
  openai: "ai_generated",
  llm: "ai_generated",
  synthetic: "ai_generated",
  model: "inferred",
  estimate: "inferred",
};

/** Federal `confidence` must stay in { confirmed, contested, speculative } for existing UI. */
const FEDERAL_CONFIDENCE = {
  HIGH: "confirmed",
  MID: "contested",
  LOW: "speculative",
};

/**
 * @param {string|undefined|null} raw
 * @returns {string} canonical slug; unknown → `inferred`
 */
function normalizeSourceType(raw) {
  const s = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (!s) return "inferred";
  if (CANONICAL_SOURCE_TYPES.includes(s)) return s;
  if (SOURCE_TYPE_ALIASES[s]) return SOURCE_TYPE_ALIASES[s];
  return "inferred";
}

/**
 * Default ledger confidence score (0–100) for country `events.confidence` INTEGER.
 * @param {string} canonicalSourceType
 */
function defaultCountryConfidenceScore(canonicalSourceType) {
  const t = normalizeSourceType(canonicalSourceType);
  const map = {
    official: 95,
    institutional: 88,
    reputable_media: 75,
    market_data: 85,
    x_signal: 45,
    inferred: 55,
    ai_generated: 40,
  };
  return map[t] ?? 55;
}

/**
 * Clamp user-provided confidence to 0–100 integer.
 * @param {unknown} n
 */
function clampConfidenceScore(n) {
  const x = Math.round(Number(n));
  if (!Number.isFinite(x)) return null;
  return Math.max(0, Math.min(100, x));
}

/**
 * Map canonical source → federal_events.confidence text.
 * @param {string} canonicalSourceType
 * @param {string|undefined} [override] confirmed|contested|speculative
 */
function normalizeFederalEventConfidence(canonicalSourceType, override) {
  const o = String(override || "")
    .trim()
    .toLowerCase();
  if (o === "confirmed" || o === "contested" || o === "speculative") return o;

  const t = normalizeSourceType(canonicalSourceType);
  if (t === "official" || t === "institutional" || t === "market_data")
    return FEDERAL_CONFIDENCE.HIGH;
  if (t === "reputable_media") return FEDERAL_CONFIDENCE.MID;
  if (t === "x_signal" || t === "inferred" || t === "ai_generated")
    return FEDERAL_CONFIDENCE.LOW;
  return FEDERAL_CONFIDENCE.MID;
}

/**
 * Types that should not drive automatic score application without review (v1 policy).
 * @param {string|undefined|null} sourceType
 */
function isLowTrustSourceType(sourceType) {
  const t = normalizeSourceType(sourceType);
  return t === "ai_generated" || t === "inferred" || t === "x_signal";
}

/**
 * Whether this event may be consumed by updateScores automatic pipeline.
 * Legacy rows (NULL source_type) remain eligible unless BLOCK env is set (handled in updateScores).
 * @param {{ source_type?: string|null }} eventRow
 */
function isEligibleForAutomaticScoreApplication(eventRow) {
  if (eventRow == null) return false;
  if (eventRow.source_type == null || eventRow.source_type === "")
    return true;
  if (isLowTrustSourceType(eventRow.source_type)) return false;
  return true;
}

/**
 * Prepare INSERT fields for country `events` from ingest payload (does not execute SQL).
 * @param {{
 *   sourceType?: string,
 *   confidenceScore?: number|null,
 *   countryCode: string,
 * }} p
 */
function prepareCountryEventSourceFields(p) {
  const source_type = normalizeSourceType(p.sourceType);
  const confidence =
    clampConfidenceScore(p.confidenceScore) ??
    defaultCountryConfidenceScore(source_type);
  return {
    source_type,
    confidence,
    _meta: {
      canonical: source_type,
      defaultScoreUsed: p.confidenceScore == null,
    },
  };
}

/**
 * Prepare federal_events source fields.
 * @param {{
 *   sourceType?: string,
 *   confidenceText?: string,
 * }} p
 */
function prepareFederalEventSourceFields(p) {
  const source_type = normalizeSourceType(p.sourceType);
  const confidence = normalizeFederalEventConfidence(
    source_type,
    p.confidenceText
  );
  return { source_type, confidence };
}

module.exports = {
  CANONICAL_SOURCE_TYPES,
  SOURCE_TYPE_ALIASES,
  FEDERAL_CONFIDENCE,
  normalizeSourceType,
  defaultCountryConfidenceScore,
  clampConfidenceScore,
  normalizeFederalEventConfidence,
  isLowTrustSourceType,
  isEligibleForAutomaticScoreApplication,
  prepareCountryEventSourceFields,
  prepareFederalEventSourceFields,
};
