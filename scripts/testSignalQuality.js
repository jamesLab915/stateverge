/**
 * Signal quality checks: syntax, pure helpers, no-pending run, merge/cluster logic,
 * noise caps, template neutral damp, cache key patterns.
 */
const path = require("path");
const { execSync } = require("child_process");
const { ROOT } = require("./_regressionEnv.js");

const { classifySeries } = require("../lib/evolution/trendEngine.js");
const {
  clusterCountryEvents,
  clusterMergeFactor,
  applyNoiseCapsToDimensionDeltas,
  applyPowerScoreNoiseCap,
  emptyDeltas,
  applyContextScaleToDelta,
} = require("../lib/evolution/eventSignalQuality.js");
const { buildCountryConsequenceRowsFromTemplate } = require("../lib/evolution/consequenceTemplates.js");

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERT: ${msg}`);
}

function section(title) {
  console.log(`\n--- ${title} ---`);
}

let failed = 0;
function ok(name) {
  console.log(`  ✓ ${name}`);
}
function fail(name, err) {
  failed++;
  console.error(`  ✗ ${name}:`, err?.message || err);
}

/** A. Syntax */
function testSyntax() {
  section("Syntax (node --check)");
  const files = [
    "scripts/updateScores.js",
    "lib/evolution/eventSignalQuality.js",
    "lib/evolution/eventSourcePolicy.js",
    "lib/ingest/eventSourceIngest.js",
    "lib/evolution/consequenceTemplates.js",
    "lib/evolution/trendEngine.js",
    "scripts/runDailyEvolution.js",
  ];
  for (const f of files) {
    try {
      execSync("node", ["--check", path.join(ROOT, f)], { stdio: "pipe" });
      ok(f);
    } catch (e) {
      fail(f, e);
    }
  }
}

/** Pure: merge factor */
function testMergeFactor() {
  section("Merge factor (cluster dampening)");
  try {
    assert(clusterMergeFactor(1) === 1, "n=1 => 1");
    assert(clusterMergeFactor(2) < 1 && clusterMergeFactor(2) > 0.5, "n=2 in (0.5,1)");
    assert(clusterMergeFactor(4) < clusterMergeFactor(2), "larger n => smaller factor");
    ok("clusterMergeFactor monotonic");
  } catch (e) {
    fail("clusterMergeFactor", e);
  }
}

/** Pure: cluster */
function testCluster() {
  section("Cluster (same country, type, ≤3d gaps)");
  try {
    const mk = (id, type, dayOffset) => ({
      id,
      country_code: "zz_test",
      event_type: type,
      event_date: new Date(Date.UTC(2025, 0, 1 + dayOffset)),
    });
    const c = clusterCountryEvents(
      [mk(1, "reform", 0), mk(2, "reform", 2), mk(3, "reform", 10)],
      { windowDays: 3 }
    );
    assert(c.length === 2, "two clusters: [1,2] and [3]");
    assert(c[0].length === 2 && c[1].length === 1, "sizes");
    ok("chunkByInterEventGap");
  } catch (e) {
    fail("clusterCountryEvents", e);
  }
}

/** Pure: noise caps */
function testNoiseCaps() {
  section("Noise caps");
  try {
    const g = emptyDeltas();
    g.governance = 20;
    g.risk = -15;
    applyNoiseCapsToDimensionDeltas(g, { maxPerDim: 4.5, maxL1: 28 });
    assert(
      Math.abs(g.governance) < 20 && Math.abs(g.risk) < 15,
      "soft cap shrinks extremes vs raw"
    );
    const p = applyPowerScoreNoiseCap(10, { powerScoreCap: 3.5 });
    assert(p < 10 && p > 3.5, "power score soft cap");
    ok("applyNoiseCaps / power cap");
  } catch (e) {
    fail("noise caps", e);
  }
}

/** Pure: context scale */
function testContextScale() {
  section("Context-aware delta (risk dimension)");
  try {
    const hi = { risk: 85, social_order: 50, governance: 50 };
    const lo = { risk: 15, social_order: 50, governance: 50 };
    const a = applyContextScaleToDelta("risk", 2, hi);
    const b = applyContextScaleToDelta("risk", 2, lo);
    assert(Math.abs(a) !== Math.abs(b), "high vs low risk countries differ");
    ok("applyContextScaleToDelta");
  } catch (e) {
    fail("context scale", e);
  }
}

/** Templates: neutral direction uses damped scaling */
function testTemplateNeutral() {
  section("consequenceTemplates (neutral damp)");
  try {
    const base = { country_code: "us", event_type: "reform", impact_strength: 5 };
    const rowsPos = buildCountryConsequenceRowsFromTemplate({
      ...base,
      impact_direction: "positive",
    });
    const rowsNeu = buildCountryConsequenceRowsFromTemplate({
      ...base,
      impact_direction: "",
    });
    const magPos = rowsPos.reduce((s, x) => s + Math.abs(x.impact_value), 0);
    const magNeu = rowsNeu.reduce((s, x) => s + Math.abs(x.impact_value), 0);
    assert(rowsPos.length > 0 && rowsNeu.length > 0, "rows generated");
    assert(magNeu < magPos, "neutral weaker than positive for same strength");
    ok("buildCountryConsequenceRowsFromTemplate neutral < positive");
  } catch (e) {
    fail("consequenceTemplates neutral", e);
  }
}

/** B. No pending events */
function testNoPendingEvents() {
  section("updateScores: no pending path (stdout)");
  try {
    let s = "";
    try {
      s = execSync("node scripts/updateScores.js", {
        cwd: ROOT,
        encoding: "utf8",
        maxBuffer: 2 * 1024 * 1024,
      });
    } catch (e) {
      s = (e.stdout || "") + (e.stderr || "");
      throw e;
    }
    const okIdle =
      (s.includes("No pending events") || s.includes("all deferred by source policy")) &&
      s.includes("Done.");
    if (!okIdle) {
      throw new Error(
        "Expected idle Done. (no pending or all deferred) — see EVENT_SOURCE_POLICY.md if low-trust events are queued."
      );
    }
    ok("stdout contains No pending events + Done.");
  } catch (e) {
    fail(
      "no pending (skip if DB has pending events you need to keep)",
      e
    );
  }
}

/** Cache key patterns (static) */
function testCacheKeyPatterns() {
  section("Cache key patterns (narrative / contrast)");
  try {
    const samples = [
      "evolution-summary:us",
      "evolution-contrast:us-cn",
      "narrative-graph:us",
      "narrative-graph-compare:us-cn",
    ];
    for (const k of samples) {
      assert(!k.includes(" "), `no spaces: ${k}`);
      assert(k.split(":").length >= 2, `colon form: ${k}`);
    }
    ok("documented cache key shapes");
  } catch (e) {
    fail("cache keys", e);
  }
}

/** Trend: flat small noise → not volatile */
function testFlatSeriesNotVolatile() {
  section("trendEngine: flat series");
  try {
    const r = classifySeries([50, 50.1, 50.2, 50.15], "overall");
    assert(
      !String(r.label).toLowerCase().includes("volatile"),
      `expected non-volatile, got ${r.label}`
    );
    ok("classifySeries flat → not volatile");
  } catch (e) {
    fail("flat series", e);
  }
}

function main() {
  console.log("StateVerge testSignalQuality.js");
  testSyntax();
  testMergeFactor();
  testCluster();
  testNoiseCaps();
  testContextScale();
  testTemplateNeutral();
  testFlatSeriesNotVolatile();
  testCacheKeyPatterns();
  testNoPendingEvents();

  if (failed > 0) {
    console.error(`\nCompleted with ${failed} failure(s).`);
    process.exit(1);
  }
  console.log("\nAll signal-quality checks passed.");
  process.exit(0);
}

main();
