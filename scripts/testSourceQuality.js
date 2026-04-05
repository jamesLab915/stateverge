/**
 * Source quality calibration tests (Event Source Layer v1).
 * No DB required; validates policy + same filter logic as updateScores.
 */
const assert = require("assert");
const {
  normalizeSourceType,
  defaultCountryConfidenceScore,
  isEligibleForAutomaticScoreApplication,
  isLowTrustSourceType,
} = require("../lib/evolution/eventSourcePolicy.js");

/** Mirrors scripts/updateScores.js active/deferred split. */
function partitionForScoring(rows, allowLowTrust) {
  const deferred = rows.filter(
    (e) => !allowLowTrust && !isEligibleForAutomaticScoreApplication(e)
  );
  const active = rows.filter(
    (e) => allowLowTrust || isEligibleForAutomaticScoreApplication(e)
  );
  return { active, deferred };
}

function row(id, source_type) {
  return { id, source_type };
}

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

console.log("StateVerge testSourceQuality.js\n");

test("normalize: departmental -> institutional", () => {
  assert.strictEqual(normalizeSourceType("departmental"), "institutional");
});

test("normalize: unknown string -> inferred", () => {
  assert.strictEqual(normalizeSourceType("totally_unknown_vendor_tag"), "inferred");
});

test("default confidence: official > ai_generated", () => {
  assert.ok(
    defaultCountryConfidenceScore("official") >
      defaultCountryConfidenceScore("ai_generated")
  );
});

const highTrustTypes = [
  "official",
  "institutional",
  "reputable_media",
  "market_data",
];
for (const t of highTrustTypes) {
  test(`eligible: ${t} (default)`, () => {
    assert.strictEqual(
      isEligibleForAutomaticScoreApplication({ source_type: t }),
      true
    );
  });
}

const lowTrustTypes = ["ai_generated", "inferred", "x_signal"];
for (const t of lowTrustTypes) {
  test(`deferred policy: ${t} not eligible without env`, () => {
    assert.strictEqual(
      isEligibleForAutomaticScoreApplication({ source_type: t }),
      false
    );
    assert.strictEqual(isLowTrustSourceType(t), true);
  });
}

test("legacy: null source_type eligible", () => {
  assert.strictEqual(isEligibleForAutomaticScoreApplication({ source_type: null }), true);
});

test("legacy: empty string source_type eligible", () => {
  assert.strictEqual(isEligibleForAutomaticScoreApplication({ source_type: "" }), true);
});

test("partition: high-trust only -> all active when allowLowTrust false", () => {
  const rows = [row(1, "official"), row(2, "market_data")];
  const { active, deferred } = partitionForScoring(rows, false);
  assert.strictEqual(active.length, 2);
  assert.strictEqual(deferred.length, 0);
});

test("partition: low-trust deferred unless allowLowTrust", () => {
  const rows = [
    row(1, "official"),
    row(2, "ai_generated"),
    row(3, "inferred"),
    row(4, "x_signal"),
  ];
  const { active, deferred } = partitionForScoring(rows, false);
  assert.strictEqual(active.length, 1);
  assert.strictEqual(deferred.length, 3);
  assert.deepStrictEqual(
    active.map((r) => r.id),
    [1]
  );
});

test("partition: allowLowTrust=1 includes low-trust", () => {
  const rows = [row(1, "ai_generated"), row(2, "inferred")];
  const { active, deferred } = partitionForScoring(rows, true);
  assert.strictEqual(active.length, 2);
  assert.strictEqual(deferred.length, 0);
});

test("partition: mixed + legacy null stays active", () => {
  const rows = [row(1, null), row(2, "ai_generated")];
  const { active, deferred } = partitionForScoring(rows, false);
  assert.strictEqual(active.length, 1);
  assert.strictEqual(active[0].id, 1);
  assert.strictEqual(deferred.length, 1);
});

test("stored alias departmental scores as institutional (eligible)", () => {
  assert.strictEqual(
    isEligibleForAutomaticScoreApplication({ source_type: "departmental" }),
    true
  );
  assert.strictEqual(isLowTrustSourceType("departmental"), false);
});

if (failed > 0) {
  console.error(`\n${failed} test(s) failed.`);
  process.exit(1);
}
console.log("\nAll source-quality tests passed.");
process.exit(0);
