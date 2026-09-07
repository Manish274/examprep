import { Hono } from "hono";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { env } from "../env.js";
import { packageRoot } from "../lib/paths.js";
import { notFound } from "../lib/errors.js";
import type { AppEnv } from "../types.js";

/**
 * A development console for exercising the pipeline by hand.
 *
 * Served from this API rather than opened as a file or hosted separately, for
 * one reason that matters: same origin. No CORS entry to maintain, and the
 * WebSocket at /ws upgrades without any extra allowance — the console speaks
 * exactly the protocol the real frontend will, with nothing loosened to let it.
 *
 * Read from disk per request rather than cached at import, so editing the page
 * only needs a browser refresh.
 */

const CONSOLE_PATH = join(packageRoot(), "public", "console.html");

export const consoleRoutes = new Hono<AppEnv>().get("/console", async (c) => {
  // Unauthenticated by design -- it is a sign-in page before it is anything
  // else, and it holds no data of its own. Everything it can reach is behind
  // the same access token as the rest of the API.
  if (env().NODE_ENV === "production") {
    // A debugging surface has no business on a production origin, however
    // harmless the page itself is.
    throw notFound("Route");
  }

  const html = await readFile(CONSOLE_PATH, "utf8");
  return c.html(html);
});
