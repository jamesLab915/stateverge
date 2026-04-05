/**
 * Real Source Adapter v1 — reputable_media (edited wire / newspaper RSS-style).
 * Structural mapping only; no HTTP in v1.
 */

const {
  DEFAULT_IMPACT_DIRECTION,
  DEFAULT_IMPACT_STRENGTH,
  pickEventDate,
  clip,
} = require("./shared.js");

/**
 * @typedef {object} ReputableMediaVendorRecordV1
 * @property {string} [countryHint] target country for the story
 * @property {string} headline
 * @property {string} [description] RSS description / dek
 * @property {string} articleUrl
 * @property {string} outletName
 * @property {string|Date} [pubDate]
 * @property {string} [section] → event_type slug
 */

/**
 * @param {ReputableMediaVendorRecordV1} record
 * @returns {Record<string, unknown>}
 */
function rawEventFromReputableMediaRecord(record) {
  const r = record && typeof record === "object" ? record : {};
  const country = r.countryHint ?? r.countryCode ?? r.target_country_code ?? "";
  const title = clip(r.headline ?? r.title ?? "", 500);
  const summary = clip(
    r.description ?? r.summary ?? r.contentSnippet ?? title,
    4000
  );
  const url = String(r.articleUrl ?? r.link ?? r.url ?? "").trim();
  const sourceName = clip(r.outletName ?? r.source_name ?? "News outlet", 200);
  const eventDate =
    pickEventDate(r.pubDate ?? r.publishedAt ?? r.event_date) ??
    pickEventDate(new Date().toISOString());
  const event_type = clip(
    r.section ?? r.event_type ?? "reputable_media_report",
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
    source_type: "reputable_media",
    source_name: sourceName,
    source_url: url,
  };
}

function sampleReputableMediaRecord() {
  return {
    countryHint: "jp",
    headline: "Cabinet approves supplementary budget for disaster relief",
    description:
      "Editors note: parliament still must vote; amounts in line with earlier leaks.",
    articleUrl: "https://www.reuters.com/world/asia-pacific/mock-japan-budget-2026",
    outletName: "Reuters",
    pubDate: "2026-04-03",
    section: "infrastructure",
    impact_direction: "positive",
    impact_strength: 5,
  };
}

module.exports = {
  rawEventFromReputableMediaRecord,
  sampleReputableMediaRecord,
};
