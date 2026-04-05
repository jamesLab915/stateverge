/**
 * Shared DB pool + project root for regression scripts (no product imports).
 */
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env.local"), override: true });

const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");

const ROOT = path.join(__dirname, "..");

function createPool() {
  return createPgPool();
}

module.exports = { ROOT, createPool };
