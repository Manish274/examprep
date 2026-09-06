import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema.js";

export type Database = ReturnType<typeof createDb>["db"];

/**
 * Creates a pooled connection. Both the API and the worker use this, so the
 * schema has exactly one definition and the two cannot drift.
 */
export function createDb(connectionString: string, poolSize = 10) {
  const client = postgres(connectionString, {
    max: poolSize,
    idle_timeout: 20,
    connect_timeout: 10,
  });
  return { db: drizzle(client, { schema }), client };
}

export { schema };
