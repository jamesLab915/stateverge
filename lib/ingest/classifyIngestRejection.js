/**
 * Map validateIncomingEvent error codes → stable categories for runner reports.
 */

/** @typedef {'missing_field'|'invalid_source_type'|'invalid_date'|'invalid_url'|'invalid_confidence'|'invalid_impact'|'invalid_reference'|'database'|'other'} IngestRejectionCategory */

/**
 * @param {string} code
 * @returns {IngestRejectionCategory}
 */
function errorCodeToCategory(code) {
  switch (code) {
    case "missing_or_empty_country_code":
    case "missing_or_empty_event_type":
    case "missing_or_empty_summary":
    case "missing_or_empty_title":
    case "missing_source_type":
    case "missing_or_empty_source_name":
    case "missing_source_url":
      return "missing_field";
    case "unrecognized_source_type":
      return "invalid_source_type";
    case "missing_or_invalid_event_date":
      return "invalid_date";
    case "source_url_must_be_http_or_https":
      return "invalid_url";
    case "invalid_confidence_not_numeric":
      return "invalid_confidence";
    case "impact_direction_must_be_positive_or_negative":
    case "impact_strength_must_be_integer_1_to_10":
      return "invalid_impact";
    case "invalid_parent_event_id":
      return "invalid_reference";
    case "db_insert_failed":
      return "database";
    default:
      return "other";
  }
}

/**
 * @param {string[]} errorCodes
 * @returns {{ categories: IngestRejectionCategory[], primary: IngestRejectionCategory }}
 */
function classifyIngestErrors(errorCodes) {
  const set = new Set();
  for (const c of errorCodes || []) {
    set.add(errorCodeToCategory(c));
  }
  const categories = [...set];
  const primary = categories[0] || "other";
  return { categories, primary };
}

module.exports = {
  errorCodeToCategory,
  classifyIngestErrors,
};
