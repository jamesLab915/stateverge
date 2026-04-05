/**
 * Real Source Adapter v1 — official (executive / primary government publications).
 * Parser is structural only; swap `record` for live API/HTML/RSS payloads later.
 */

const {
  DEFAULT_IMPACT_DIRECTION,
  DEFAULT_IMPACT_STRENGTH,
  pickEventDate,
  clip,
} = require("./shared.js");

/**
 * @typedef {object} OfficialVendorRecordV1
 * @property {string} [jurisdiction] country code, e.g. us
 * @property {string} [countryCode] alias
 * @property {string} headline
 * @property {string} [summary] body or dek
 * @property {string} canonicalUrl https URL
 * @property {string} issuerDisplayName e.g. ministry name
 * @property {string|Date} [issuedAt] ISO or date
 * @property {string} [topicSlug] maps to event_type
 * @property {number} [impact_strength] 1–10 override
 * @property {'positive'|'negative'} [impact_direction] override
 */

/**
 * Map vendor official record → raw object for Ingest Contract (validateIncomingEvent).
 * @param {OfficialVendorRecordV1} record
 * @returns {Record<string, unknown>}
 */
function rawEventFromOfficialRecord(record) {
  const r = record && typeof record === "object" ? record : {};
  const country =
    r.jurisdiction ?? r.countryCode ?? r.country_code ?? "";
  const title = clip(r.headline ?? r.title ?? "", 500);
  const summary = clip(
    r.summary ?? r.bodyText ?? r.dek ?? title,
    4000
  );
  const url = String(r.canonicalUrl ?? r.url ?? "").trim();
  const sourceName = clip(
    r.issuerDisplayName ?? r.sourceName ?? "Official source",
    200
  );
  const eventDate =
    pickEventDate(r.issuedAt ?? r.publishedAt ?? r.event_date) ??
    pickEventDate(new Date().toISOString());
  const event_type = clip(
    r.topicSlug ?? r.event_type ?? "official_publication",
    120
  );

  return {
    target_country_code: String(country).trim().toLowerCase(),
    event_type,
    title,
    summary: summary || title,
    event_date: eventDate,
    impact_direction: r.impact_direction ?? DEFAULT_IMPACT_DIRECTION,
    impact_strength: r.impact_strength ?? DEFAULT_IMPACT_STRENGTH,
    source_type: "official",
    source_name: sourceName,
    source_url: url,
  };
}

/** Mock shape for tests / local dev (no network). */
function sampleOfficialRecord() {
  return {
    jurisdiction: "us",
    headline: "Executive order on supply chain review",
    summary:
      "Orders a 90-day review of critical supply chains with agency reports due to the President.",
    canonicalUrl: "https://www.whitehouse.gov/briefing-room/policies/mock-path",
    issuerDisplayName: "The White House",
    issuedAt: "2026-04-02",
    topicSlug: "economy_policy",
    impact_direction: "negative",
    impact_strength: 4,
  };
}

module.exports = {
  rawEventFromOfficialRecord,
  sampleOfficialRecord,
};
