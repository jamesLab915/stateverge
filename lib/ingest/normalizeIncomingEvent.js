/**
 * Ingest Contract v1 — normalize arbitrary raw event payloads to a stable shape
 * (snake_case, canonical source_type, clamped confidence).
 * Does not validate required fields; use validateIncomingEvent.js for that.
 */

const {
  normalizeSourceType,
  prepareCountryEventSourceFields,
} = require("../evolution/eventSourcePolicy.js");

const TITLE_MAX = 500;
const SUMMARY_MAX = 4000;
const SOURCE_NAME_MAX = 200;

/**
 * @param {unknown} raw
 * @returns {string}
 */
function collapseWs(s) {
  return String(s ?? "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * @param {Record<string, unknown>} r
 * @param {string[]} keys
 * @returns {unknown}
 */
function firstDefined(r, keys) {
  for (const k of keys) {
    if (r[k] !== undefined && r[k] !== null && r[k] !== "") return r[k];
  }
  return undefined;
}

/**
 * @param {unknown} raw
 * @returns {string|null} YYYY-MM-DD or null
 */
function normalizeEventDate(raw) {
  if (raw === undefined || raw === null || raw === "") return null;
  if (raw instanceof Date && !Number.isNaN(raw.getTime())) {
    return raw.toISOString().slice(0, 10);
  }
  const s = String(raw).trim();
  if (!s) return null;
  const ymd = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (ymd) return `${ymd[1]}-${ymd[2]}-${ymd[3]}`;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}

/**
 * @param {unknown} v
 * @returns {number|null}
 */
function toInt1to10(v) {
  const n = Math.round(Number(v));
  if (!Number.isFinite(n)) return null;
  return n;
}

/**
 * @param {unknown} raw
 * @returns {Record<string, unknown>}
 */
function normalizeIncomingEvent(raw) {
  const r =
    raw !== null && typeof raw === "object" && !Array.isArray(raw)
      ? /** @type {Record<string, unknown>} */ (raw)
      : {};

  const countryRaw = firstDefined(r, [
    "target_country_code",
    "country_code",
    "countryCode",
  ]);
  const country_code =
    countryRaw === undefined || countryRaw === null
      ? ""
      : String(countryRaw).trim().toLowerCase();

  const event_type = collapseWs(
    firstDefined(r, ["event_type", "eventType"]) ?? ""
  );

  let title = collapseWs(firstDefined(r, ["title", "headline"]) ?? "");
  let summary = collapseWs(firstDefined(r, ["summary", "description"]) ?? "");
  if (!summary && title) summary = title;
  if (!title && summary) title = summary.slice(0, TITLE_MAX);
  if (title.length > TITLE_MAX) title = title.slice(0, TITLE_MAX);
  if (summary.length > SUMMARY_MAX) summary = summary.slice(0, SUMMARY_MAX);

  const event_date = normalizeEventDate(
    firstDefined(r, ["event_date", "eventDate"])
  );

  const impactRaw = firstDefined(r, ["impact_direction", "impactDirection"]);
  const impact_direction =
    impactRaw === undefined || impactRaw === null
      ? ""
      : String(impactRaw).trim().toLowerCase();

  const impact_strength = toInt1to10(
    firstDefined(r, ["impact_strength", "impactStrength"])
  );

  const sourceTypeRaw = firstDefined(r, ["source_type", "sourceType"]);
  const source_type = normalizeSourceType(sourceTypeRaw);

  let source_name = collapseWs(firstDefined(r, ["source_name", "sourceName"]) ?? "");
  if (source_name.length > SOURCE_NAME_MAX)
    source_name = source_name.slice(0, SOURCE_NAME_MAX);

  let source_url = String(
    firstDefined(r, ["source_url", "sourceUrl", "url"]) ?? ""
  ).trim();

  const confRaw = firstDefined(r, ["confidence", "confidenceScore"]);
  const srcFields = prepareCountryEventSourceFields({
    countryCode: country_code || "xx",
    sourceType: source_type,
    confidenceScore:
      confRaw === undefined || confRaw === null || confRaw === ""
        ? null
        : confRaw,
  });

  const parentRaw = firstDefined(r, ["parent_event_id", "parentEventId"]);
  let parent_event_id = null;
  if (parentRaw !== undefined && parentRaw !== null && parentRaw !== "") {
    const p = Math.round(Number(parentRaw));
    if (Number.isFinite(p) && p > 0) parent_event_id = p;
  }

  const pa = r.primary_actor ?? r.primaryActor;
  let primary_actor = null;
  if (pa !== null && pa !== undefined && typeof pa === "object" && !Array.isArray(pa)) {
    const o = /** @type {Record<string, unknown>} */ (pa);
    primary_actor = {
      name: collapseWs(o.name ?? ""),
      actor_type: collapseWs(o.actor_type ?? o.actorType ?? "coalition"),
      role: collapseWs(o.role ?? "influenced"),
      summary: o.summary
        ? collapseWs(String(o.summary)).slice(0, 500)
        : null,
    };
    if (!primary_actor.name) primary_actor = null;
  }

  return {
    country_code,
    event_type,
    title,
    summary,
    event_date,
    impact_direction,
    impact_strength,
    source_type: srcFields.source_type,
    confidence: srcFields.confidence,
    source_name,
    source_url,
    parent_event_id,
    primary_actor,
  };
}

module.exports = {
  normalizeIncomingEvent,
  normalizeEventDate,
  collapseWs,
};
