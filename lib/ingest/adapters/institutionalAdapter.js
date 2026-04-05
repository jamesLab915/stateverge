/**
 * Real Source Adapter v1 — institutional (agencies, legislatures, IGOs, central banks).
 * Structural parser only; plug in ECB/Fed RSS, UN press, etc. later.
 */

const {
  DEFAULT_IMPACT_DIRECTION,
  DEFAULT_IMPACT_STRENGTH,
  pickEventDate,
  clip,
} = require("./shared.js");

/**
 * @typedef {object} InstitutionalVendorRecordV1
 * @property {string} [countryCode]
 * @property {string} [targetCountryCode]
 * @property {string} title
 * @property {string} [abstractText]
 * @property {string} link
 * @property {string} organizationName
 * @property {string|Date} [pubDate]
 * @property {string} [category] → event_type
 */

/**
 * @param {InstitutionalVendorRecordV1} record
 * @returns {Record<string, unknown>}
 */
function rawEventFromInstitutionalRecord(record) {
  const r = record && typeof record === "object" ? record : {};
  const country =
    r.targetCountryCode ?? r.countryCode ?? r.jurisdiction ?? "";
  const title = clip(r.title ?? r.headline ?? "", 500);
  const summary = clip(
    r.abstractText ?? r.summary ?? r.description ?? title,
    4000
  );
  const url = String(r.link ?? r.url ?? "").trim();
  const sourceName = clip(
    r.organizationName ?? r.source_name ?? "Institutional source",
    200
  );
  const eventDate =
    pickEventDate(r.pubDate ?? r.publishedAt ?? r.event_date) ??
    pickEventDate(new Date().toISOString());
  const event_type = clip(
    r.category ?? r.event_type ?? "institutional_notice",
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
    source_type: "institutional",
    source_name: sourceName,
    source_url: url,
  };
}

function sampleInstitutionalRecord() {
  return {
    countryCode: "eu",
    title: "ECB staff macroeconomic projections update",
    abstractText:
      "Projected inflation path revised slightly downward for the next two years in the baseline scenario.",
    link: "https://www.ecb.europa.eu/press/pr/date/2026/html/mock-example.en.html",
    organizationName: "European Central Bank",
    pubDate: "2026-04-01",
    category: "economy_policy",
    impact_direction: "positive",
    impact_strength: 3,
  };
}

module.exports = {
  rawEventFromInstitutionalRecord,
  sampleInstitutionalRecord,
};
