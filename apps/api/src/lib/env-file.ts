import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { repoRoot } from "./paths.js";

/**
 * Side-effect module: loads the repo-root .env into process.env.
 *
 * The entrypoint imports this FIRST, so the file is loaded before any other
 * module reads the environment at import time -- module evaluation follows
 * import order, so a function call placed among the imports would run too
 * late. Never imported by env.ts itself: tests construct their own
 * environment and must not have a developer's local .env leak into them.
 *
 * Node's own parser, and it never replaces a variable already set.
 */
const envPath = resolve(repoRoot(), ".env");
if (existsSync(envPath)) {
  process.loadEnvFile(envPath);
}
