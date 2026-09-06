import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import postgres from "postgres";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, "../../../.env");
if (existsSync(envPath)) {
  process.loadEnvFile(envPath);
}

async function main(): Promise<void> {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error("DATABASE_URL is not set");
  }

  // A migration connection is short-lived and must not be pooled.
  const client = postgres(url, { max: 1 });
  try {
    await migrate(drizzle(client), {
      migrationsFolder: resolve(here, "../drizzle"),
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
