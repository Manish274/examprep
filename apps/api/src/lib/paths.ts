import { fileURLToPath } from "node:url";
import { dirname, isAbsolute, resolve } from "node:path";

/**
 * Absolute path to the repository root.
 *
 * Deriving this from the module's own location rather than process.cwd() is
 * deliberate: npm workspace scripts run with the working directory set to the
 * package, so "./storage/uploads" from a shared .env would resolve to
 * apps/api/storage/uploads here and to the repo root in the Python service.
 * The two would then disagree about where an uploaded file lives.
 *
 * Depth is the same from source and from build output, since src/lib/x.ts
 * compiles to dist/lib/x.js.
 */
export function repoRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "../../../..");
}

/** Resolves a possibly-relative configured path against the repo root. */
export function fromRepoRoot(path: string): string {
  return isAbsolute(path) ? path : resolve(repoRoot(), path);
}
