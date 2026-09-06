import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { env } from "../env.js";
import * as schema from "./schema.js";

export type Database = ReturnType<typeof createDb>["db"];

export function createDb(connectionString: string = env().DATABASE_URL) {
  const client = postgres(connectionString, {
    max: 10,
    idle_timeout: 20,
    connect_timeout: 10,
  });
  const db = drizzle(client, { schema });
  return { db, client };
}

let singleton: ReturnType<typeof createDb> | null = null;

export function db(): Database {
  singleton ??= createDb();
  return singleton.db;
}

export async function closeDb(): Promise<void> {
  if (singleton) {
    await singleton.client.end({ timeout: 5 });
    singleton = null;
  }
}

export { schema };
