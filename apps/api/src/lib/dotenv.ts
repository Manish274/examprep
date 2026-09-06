import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { repoRoot } from "./paths.js";

/**
 * Loads the repo-root .env into process.env using Node's built-in parser.
 *
 * Called only from entrypoints, never from env.ts — tests construct their own
 * environment and must not have a developer's local .env leak into them.
 */
export function loadDotEnv(): void {
  const envPath = resolve(repoRoot(), ".env");
  if (existsSync(envPath)) {
    process.loadEnvFile(envPath);
  }
}
