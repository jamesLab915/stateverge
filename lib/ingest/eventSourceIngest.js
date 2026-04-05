/**
 * Ingest entrypoint: re-exports Event Source Layer v1 helpers.
 * Use these when wiring RSS, APIs, or batch loaders so source_type / confidence stay canonical.
 */
module.exports = require("../evolution/eventSourcePolicy.js");
