/**
 * Mandatory pipeline: adapter raw → normalize → validate.
 * Do not INSERT into `events` without a passing validation (ok === true).
 */

const { normalizeIncomingEvent } = require("../normalizeIncomingEvent.js");
const { validateIncomingEvent } = require("../validateIncomingEvent.js");

/**
 * @param {unknown} rawFromAdapter output of rawEventFrom*Record()
 * @returns {{
 *   normalized: ReturnType<typeof normalizeIncomingEvent>,
 *   validation: ReturnType<typeof validateIncomingEvent>,
 * }}
 */
function throughIngestContract(rawFromAdapter) {
  return {
    normalized: normalizeIncomingEvent(rawFromAdapter),
    validation: validateIncomingEvent(rawFromAdapter),
  };
}

module.exports = { throughIngestContract };
