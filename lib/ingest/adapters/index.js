/**
 * Real Source Adapters v1 — re-export only; all paths go through Ingest Contract.
 */

const { rawEventFromOfficialRecord, sampleOfficialRecord } = require("./officialAdapter.js");
const {
  rawEventFromInstitutionalRecord,
  sampleInstitutionalRecord,
} = require("./institutionalAdapter.js");
const {
  rawEventFromReputableMediaRecord,
  sampleReputableMediaRecord,
} = require("./reputableMediaAdapter.js");
const { throughIngestContract } = require("./throughIngestContract.js");
const shared = require("./shared.js");

module.exports = {
  rawEventFromOfficialRecord,
  sampleOfficialRecord,
  rawEventFromInstitutionalRecord,
  sampleInstitutionalRecord,
  rawEventFromReputableMediaRecord,
  sampleReputableMediaRecord,
  throughIngestContract,
  adapterShared: shared,
};
