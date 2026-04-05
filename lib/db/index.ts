import { Pool } from "pg";
import { createPgPool } from "./pgPool.js";

const globalForPg = globalThis as unknown as {
  pgPool?: Pool;
};

export const pool = globalForPg.pgPool ?? createPgPool();

if (process.env.NODE_ENV !== "production") {
  globalForPg.pgPool = pool;
}
