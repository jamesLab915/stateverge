/**
 * Curated high-trust feeds for Source Onboarding v1.
 * Each entry maps to an existing Real Source Adapter + Ingest Contract path.
 */

const {
  rawEventFromOfficialRecord,
} = require("../ingest/adapters/officialAdapter.js");
const {
  rawEventFromInstitutionalRecord,
} = require("../ingest/adapters/institutionalAdapter.js");
const {
  rawEventFromReputableMediaRecord,
} = require("../ingest/adapters/reputableMediaAdapter.js");

/**
 * @typedef {{ title: string, link: string, pubDate: string, description: string }} RssItem
 */

/**
 * @param {RssItem} item
 * @param {{ defaultCountry: string, issuer: string, topic?: string }} ctx
 */
function itemToOfficial(item, ctx) {
  const url = String(item.link || "").trim();
  return rawEventFromOfficialRecord({
    jurisdiction: ctx.defaultCountry,
    headline: item.title || "Untitled",
    summary: item.description || item.title,
    canonicalUrl: url.startsWith("http") ? url : `https://www.whitehouse.gov${url}`,
    issuerDisplayName: ctx.issuer,
    issuedAt: item.pubDate || new Date().toISOString(),
    topicSlug: ctx.topic || "official_publication",
  });
}

/**
 * @param {RssItem} item
 * @param {{ defaultCountry: string, org: string, topic?: string }} ctx
 */
function itemToInstitutional(item, ctx) {
  const url = String(item.link || "").trim();
  return rawEventFromInstitutionalRecord({
    targetCountryCode: ctx.defaultCountry,
    title: item.title || "Untitled",
    abstractText: item.description || item.title,
    link: url.startsWith("http") ? url : `https://www.federalreserve.gov${url}`,
    organizationName: ctx.org,
    pubDate: item.pubDate || new Date().toISOString(),
    category: ctx.topic || "institutional_notice",
  });
}

/**
 * @param {RssItem} item
 * @param {{ defaultCountry: string, outlet: string, topic?: string }} ctx
 */
function itemToReputableMedia(item, ctx) {
  const url = String(item.link || "").trim();
  return rawEventFromReputableMediaRecord({
    countryHint: ctx.defaultCountry,
    headline: item.title || "Untitled",
    description: item.description || item.title,
    articleUrl: url.startsWith("http") ? url : `https://www.bbc.co.uk${url}`,
    outletName: ctx.outlet,
    pubDate: item.pubDate || new Date().toISOString(),
    section: ctx.topic || "reputable_media_report",
  });
}

/** @type {Record<string, {
 *   id: string,
 *   displayName: string,
 *   feedUrl: string,
 *   source_type: string,
 *   adapter: 'official'|'institutional'|'reputable_media',
 *   defaultCountry: string,
 *   fieldGaps: string[],
 *   toRaw: (item: RssItem) => Record<string, unknown>,
 * }>} */
const CURATED_SOURCES = {
  whitehouse: {
    id: "whitehouse",
    displayName: "The White House (RSS)",
    feedUrl: "https://www.whitehouse.gov/news/feed/",
    source_type: "official",
    adapter: "official",
    defaultCountry: "us",
    fieldGaps: [
      "No structured impact_direction from RSS; adapter uses defaults (positive / 5) unless overridden.",
      "event_type is a coarse slug (official_publication); topic classification not in feed.",
    ],
    toRaw: (item) =>
      itemToOfficial(item, {
        defaultCountry: "us",
        issuer: "The White House",
        topic: "official_publication",
      }),
  },
  fed_press: {
    id: "fed_press",
    displayName: "Federal Reserve — All Press Releases (RSS)",
    feedUrl: "https://www.federalreserve.gov/feeds/press_all.xml",
    source_type: "institutional",
    adapter: "institutional",
    defaultCountry: "us",
    fieldGaps: [
      "RSS omits directional macro impact; defaults used for impact_strength / impact_direction.",
      "Country fixed to US for Fed institutional events in v1.",
    ],
    toRaw: (item) =>
      itemToInstitutional(item, {
        defaultCountry: "us",
        org: "Federal Reserve Board",
        topic: "economy_policy",
      }),
  },
  bbc_world: {
    id: "bbc_world",
    displayName: "BBC News — World (RSS)",
    feedUrl: "https://feeds.bbci.co.uk/news/world/rss.xml",
    source_type: "reputable_media",
    adapter: "reputable_media",
    defaultCountry: "gb",
    fieldGaps: [
      "World feed is multi-country; v1 pins target_country_code to gb (UK outlet). Override with --country= on CLI.",
      "Headline-only items may duplicate summary; no dedicated impact signal in feed.",
    ],
    toRaw: (item) =>
      itemToReputableMedia(item, {
        defaultCountry: "gb",
        outlet: "BBC News",
        topic: "reputable_media_report",
      }),
  },
};

function listCuratedSourceIds() {
  return Object.keys(CURATED_SOURCES).sort();
}

/**
 * @param {string} id
 */
function getCuratedSource(id) {
  const k = String(id || "")
    .trim()
    .toLowerCase();
  return CURATED_SOURCES[k] || null;
}

module.exports = {
  CURATED_SOURCES,
  listCuratedSourceIds,
  getCuratedSource,
};
