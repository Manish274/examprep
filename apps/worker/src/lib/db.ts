import { createDb, type Database } from "@examprep/db";
import { env } from "../env.js";

let singleton: ReturnType<typeof createDb> | null = null;

export function db(): Database {
  // A worker holds far fewer concurrent queries than the API, so a small pool
  // is enough and leaves connections for the API under a shared Postgres.
  singleton ??= createDb(env().DATABASE_URL, 5);
  return singleton.db;
}

export async function closeDb(): Promise<void> {
  if (singleton) {
    await singleton.client.end({ timeout: 5 });
    singleton = null;
  }
}
