import { createDb, type Database } from "@examprep/db";
import { env } from "../env.js";

let singleton: ReturnType<typeof createDb> | null = null;

export function db(): Database {
  singleton ??= createDb(env().DATABASE_URL);
  return singleton.db;
}

export async function closeDb(): Promise<void> {
  if (singleton) {
    await singleton.client.end({ timeout: 5 });
    singleton = null;
  }
}
