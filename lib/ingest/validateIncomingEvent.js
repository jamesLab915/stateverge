/**
 * Ingest Contract v1 — validate normalized payloads; strict source_type recognition.
 */

const {
  clampConfidenceScore,
  prepareCountryEventSourceFields,
  isEligibleForAutomaticScoreApplication,
  CANONICAL_SOURCE_TYPES,
  SOURCE_TYPE_ALIASES,
} = require("../evolution/eventSourcePolicy.js");
const { normalizeIncomingEvent } = require("./normalizeIncomingEvent.js");

/**
 * True if raw maps to a canonical type or a registered alias (not the unknown→inferred path).
 * @param {unknown} raw
 */
function isRecognizedSourceTypeInput(raw) {
  const s = String(raw ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (!s) return false;
  if (CANONICAL_SOURCE_TYPES.includes(s)) return true;
  return Object.prototype.hasOwnProperty.call(SOURCE_TYPE_ALIASES, s);
}

/**
 * @param {unknown} raw
 * @returns {{ ok: true, value: object, warnings: string[], layer: 'truth'|'signal' } | { ok: false, errors: string[], warnings: string[], value?: object }}
 */
function validateIncomingEvent(raw) {
  const warnings = [];
  const errors = [];
  const n = normalizeIncomingEvent(raw);

  const r =
    raw !== null && typeof raw === "object" && !Array.isArray(raw)
      ? /** @type {Record<string, unknown>} */ (raw)
      : {};

  const confRaw = r.confidence ?? r.confidenceScore;
  const hasConf =
    confRaw !== undefined &&
    confRaw !== null &&
    String(confRaw).trim() !== "";

  /** @type {number|null} */
  let explicitConfidence = null;
  if (hasConf) {
    explicitConfidence = clampConfidenceScore(confRaw);
    if (explicitConfidence === null) {
      errors.push("invalid_confidence_not_numeric");
    } else {
      const rawNum = Number(confRaw);
      if (
        Number.isFinite(rawNum) &&
        (Math.round(rawNum) < 0 || Math.round(rawNum) > 100)
      ) {
        warnings.push("confidence_out_of_range_was_clamped");
      }
    }
  }

  if (!n.country_code) errors.push("missing_or_empty_country_code");
  if (!n.event_type) errors.push("missing_or_empty_event_type");
  if (!n.summary) errors.push("missing_or_empty_summary");
  if (!n.title) errors.push("missing_or_empty_title");
  if (!n.event_date) errors.push("missing_or_invalid_event_date");

  const stRaw = r.source_type ?? r.sourceType;
  if (stRaw === undefined || stRaw === null || String(stRaw).trim() === "") {
    errors.push("missing_source_type");
  } else if (!isRecognizedSourceTypeInput(stRaw)) {
    errors.push("unrecognized_source_type");
  }

  if (!n.source_name) errors.push("missing_or_empty_source_name");

  const url = n.source_url;
  if (!url) errors.push("missing_source_url");
  else if (!/^https?:\/\//i.test(url)) errors.push("source_url_must_be_http_or_https");

  if (n.impact_direction !== "positive" && n.impact_direction !== "negative") {
    errors.push("impact_direction_must_be_positive_or_negative");
  }
  if (
    n.impact_strength === null ||
    n.impact_strength < 1 ||
    n.impact_strength > 10
  ) {
    errors.push("impact_strength_must_be_integer_1_to_10");
  }

  if (r.parent_event_id !== undefined || r.parentEventId !== undefined) {
    const p = r.parent_event_id ?? r.parentEventId;
    if (n.parent_event_id === null && p !== undefined && p !== null && p !== "") {
      errors.push("invalid_parent_event_id");
    }
  }

  const rawPa = r.primary_actor ?? r.primaryActor;
  if (rawPa && typeof rawPa === "object" && !Array.isArray(rawPa)) {
    const o = /** @type {Record<string, unknown>} */ (rawPa);
    if (!o.actor_type && !o.actorType)
      warnings.push("primary_actor_actor_type_defaulted");
    if (!o.role) warnings.push("primary_actor_role_defaulted");
  }

  if (errors.length > 0) {
    return { ok: false, errors, warnings, value: n };
  }

  const prepared = prepareCountryEventSourceFields({
    countryCode: n.country_code,
    sourceType: n.source_type,
    confidenceScore: hasConf ? explicitConfidence : null,
  });

  const merged = {
    ...n,
    source_type: prepared.source_type,
    confidence: prepared.confidence,
  };

  const layer = isEligibleForAutomaticScoreApplication({
    source_type: merged.source_type,
  })
    ? "truth"
    : "signal";

  return {
    ok: true,
    value: merged,
    warnings,
    layer,
  };
}

module.exports = {
  validateIncomingEvent,
  isRecognizedSourceTypeInput,
};
