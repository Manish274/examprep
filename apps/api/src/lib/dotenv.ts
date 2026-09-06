import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

/**
 * Loads the repo-root .env into process.env using Node's built-in parser.
 *
 * Called only from entrypoints, never from env.ts — tests construct their own
 * environment and must not have a developer's local .env leak into them.
 */
export function loadDotEnv(): void {
  const here = dirname(fileURLToPath(import.meta.url));
  const envPath = resolve(here, "../../../../.env");
  if (existsSync(envPath)) {
    process.loadEnvFile(envPath);
  }
}
