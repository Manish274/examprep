import "../lib/env-file.js";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import postgres from "postgres";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadEnv } from "../env.js";


const here = dirname(fileURLToPath(import.meta.url));

async function main(): Promise<void> {
  const env = loadEnv();
  // A migration connection is short-lived and must not be pooled.
  const client = postgres(env.DATABASE_URL, { max: 1 });
  try {
    await migrate(drizzle(client), {
      migrationsFolder: resolve(here, "../../drizzle"),
    });
    console.log("migrations applied");
  } finally {
    await client.end();
  }
}

main().catch((err: unknown) => {
  console.error("migration failed:", err);
  process.exit(1);
});
