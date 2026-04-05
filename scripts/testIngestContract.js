/**
 * Ingest Contract v1 — sample validations (no DB).
 */
const assert = require("assert");
const { normalizeIncomingEvent } = require("../lib/ingest/normalizeIncomingEvent.js");
const {
  validateIncomingEvent,
  isRecognizedSourceTypeInput,
} = require("../lib/ingest/validateIncomingEvent.js");

const base = {
  target_country_code: "US",
  event_type: "trade_policy",
  title: "Tariff announcement",
  summary: "Government announces adjusted tariff schedule.",
  event_date: "2026-04-01",
  impact_direction: "negative",
  impact_strength: 6,
  source_name: "Example Ministry",
  source_url: "https://example.gov/news/tariffs",
};

let failed = 0;
function test(name, fn) {
  try {
    fn();
    console.log("  ✓", name);
  } catch (e) {
    failed++;
    console.error("  ✗", name, e.message);
  }
}

console.log("StateVerge testIngestContract.js\n");

test("official → valid, truth layer", () => {
  const r = validateIncomingEvent({ ...base, source_type: "official" });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.layer, "truth");
  assert.strictEqual(r.value.source_type, "official");
  assert.strictEqual(r.value.country_code, "us");
});

test("institutional → valid, truth layer", () => {
  const r = validateIncomingEvent({
    ...base,
    sourceType: "institutional",
    confidence: 88,
  });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.layer, "truth");
  assert.strictEqual(r.value.confidence, 88);
});

test("reputable_media (alias news) → valid, truth layer", () => {
  const r = validateIncomingEvent({ ...base, source_type: "news" });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.layer, "truth");
  assert.strictEqual(r.value.source_type, "reputable_media");
});

test("ai_generated → valid, signal layer", () => {
  const r = validateIncomingEvent({ ...base, source_type: "ai_generated" });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.layer, "signal");
});

test("malformed: missing country", () => {
  const { target_country_code, ...rest } = base;
  const r = validateIncomingEvent({ ...rest, source_type: "official" });
  assert.strictEqual(r.ok, false);
  assert.ok(r.errors.includes("missing_or_empty_country_code"));
});

test("malformed: unknown source_type", () => {
  const r = validateIncomingEvent({
    ...base,
    source_type: "totally_unknown_vendor",
  });
  assert.strictEqual(r.ok, false);
  assert.ok(r.errors.includes("unrecognized_source_type"));
});

test("malformed: bad URL", () => {
  const r = validateIncomingEvent({
    ...base,
    source_type: "official",
    source_url: "ftp://bad",
  });
  assert.strictEqual(r.ok, false);
  assert.ok(r.errors.includes("source_url_must_be_http_or_https"));
});

test("malformed: invalid confidence", () => {
  const r = validateIncomingEvent({
    ...base,
    source_type: "official",
    confidence: "not-a-number",
  });
  assert.strictEqual(r.ok, false);
  assert.ok(r.errors.includes("invalid_confidence_not_numeric"));
});

test("normalize: departmental → institutional in output", () => {
  const n = normalizeIncomingEvent({
    ...base,
    source_type: "departmental",
  });
  assert.strictEqual(n.source_type, "institutional");
});

test("isRecognizedSourceTypeInput: unknown false, canonical true", () => {
  assert.strictEqual(isRecognizedSourceTypeInput("official"), true);
  assert.strictEqual(isRecognizedSourceTypeInput("bogus_label"), false);
});

if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
}
console.log("\nAll ingest-contract tests passed.");
process.exit(0);
