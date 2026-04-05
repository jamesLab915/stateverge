/**
 * Real Source Adapters v1 — ingest contract smoke tests (no DB).
 */
const assert = require("assert");
const {
  rawEventFromOfficialRecord,
  sampleOfficialRecord,
  rawEventFromInstitutionalRecord,
  sampleInstitutionalRecord,
  rawEventFromReputableMediaRecord,
  sampleReputableMediaRecord,
  throughIngestContract,
} = require("../lib/ingest/adapters/index.js");

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

console.log("StateVerge testRealSourceAdapters.js\n");

test("official sample → ingest contract ok, truth layer", () => {
  const raw = rawEventFromOfficialRecord(sampleOfficialRecord());
  const { normalized, validation } = throughIngestContract(raw);
  assert.strictEqual(validation.ok, true);
  assert.strictEqual(validation.layer, "truth");
  assert.strictEqual(normalized.source_type, "official");
  assert.strictEqual(validation.value.source_type, "official");
  assert.ok(String(validation.value.source_url).startsWith("https://"));
});

test("institutional sample → ingest contract ok, truth layer", () => {
  const raw = rawEventFromInstitutionalRecord(sampleInstitutionalRecord());
  const { validation } = throughIngestContract(raw);
  assert.strictEqual(validation.ok, true);
  assert.strictEqual(validation.layer, "truth");
  assert.strictEqual(validation.value.country_code, "eu");
});

test("reputable_media sample → ingest contract ok, truth layer", () => {
  const raw = rawEventFromReputableMediaRecord(sampleReputableMediaRecord());
  const { validation } = throughIngestContract(raw);
  assert.strictEqual(validation.ok, true);
  assert.strictEqual(validation.layer, "truth");
  assert.strictEqual(validation.value.source_type, "reputable_media");
});

test("adapters use defaults when impact omitted", () => {
  const raw = rawEventFromOfficialRecord({
    jurisdiction: "us",
    headline: "Test",
    summary: "Body.",
    canonicalUrl: "https://example.gov/x",
    issuerDisplayName: "Gov",
    issuedAt: "2026-01-15",
    topicSlug: "trade_policy",
  });
  const { validation } = throughIngestContract(raw);
  assert.strictEqual(validation.ok, true);
  assert.strictEqual(validation.value.impact_strength, 5);
  assert.strictEqual(validation.value.impact_direction, "positive");
});

test("pipeline exposes normalized + validation together", () => {
  const raw = rawEventFromReputableMediaRecord(sampleReputableMediaRecord());
  const out = throughIngestContract(raw);
  assert.ok(out.normalized);
  assert.ok(out.validation);
  assert.strictEqual(out.normalized.country_code, out.validation.value.country_code);
});

if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
}
console.log("\nAll real-source-adapter tests passed.");
process.exit(0);
