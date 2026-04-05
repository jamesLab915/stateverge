/**
 * Trend stability: classifySeries should not label small noise as volatile;
 * monotonic drift should move toward rising/declining, not flip randomly.
 */
const { classifySeries } = require("../lib/evolution/trendEngine.js");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function main() {
  console.log("StateVerge testTrendStability.js\n");
  let failed = 0;

  const flat = classifySeries([50, 50.1, 50.05, 50.2], "overall");
  if (String(flat.label).toLowerCase().includes("volatile")) {
    console.error("FAIL: flat micro noise should not be volatile:", flat.label);
    failed++;
  } else {
    console.log("✓ flat micro series → not volatile:", flat.label);
  }

  const rise = classifySeries([48, 49, 50, 52, 54], "overall");
  if (String(rise.label).toLowerCase().includes("volatile")) {
    console.error("FAIL: smooth monotonic rise should not be volatile:", rise.label);
    failed++;
  } else {
    console.log("✓ smooth rising series label:", rise.label);
  }

  const fall = classifySeries([54, 53, 51, 49, 47], "overall");
  if (String(fall.label).toLowerCase().includes("volatile")) {
    console.error("FAIL: smooth monotonic fall should not be volatile:", fall.label);
    failed++;
  } else {
    console.log("✓ smooth declining series label:", fall.label);
  }

  const a = classifySeries([50, 50, 50, 50], "overall");
  assert(String(a.label).includes("stable"), "flat equal → stable");
  console.log("✓ constant series → stable");

  if (failed) {
    console.error(`\nFailed: ${failed}`);
    process.exit(1);
  }
  console.log("\nTrend stability checks passed.");
  process.exit(0);
}

main();
