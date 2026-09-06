import { Hono } from "hono";
import { sql } from "drizzle-orm";
import { db } from "../lib/db.js";
import { redis } from "../lib/redis.js";
import { ragHealth } from "../lib/rag-client.js";
import { logger } from "../lib/logger.js";
import type { AppEnv } from "../types.js";

type Check = { name: string; ok: boolean; detail?: string };

/**
 * Some drivers throw errors with an empty `message` and the real cause on
 * `code` (postgres.js does this for a refused connection), so fall back
 * through code and name rather than reporting a blank failure.
 */
function describe(err: unknown): string {
  if (!(err instanceof Error)) return String(err);
  const code = (err as NodeJS.ErrnoException).code;
  return err.message || code || err.name || "unknown error";
}

async function check(name: string, fn: () => Promise<unknown>): Promise<Check> {
  try {
    await fn();
    return { name, ok: true };
  } catch (err) {
    const detail = describe(err);
    logger.warn({ name, detail }, "health check failed");
    return { name, ok: false, detail };
  }
}

export const healthRoutes = new Hono<AppEnv>()
  /** Liveness — is the process up? Never touches dependencies. */
  .get("/health", (c) =>
    c.json({ status: "ok", service: "api", uptime: process.uptime() }),
  )
  /** Readiness — can this instance actually serve traffic? */
  .get("/health/ready", async (c) => {
    const checks = await Promise.all([
      check("postgres", () => db().execute(sql`select 1`)),
      check("redis", () => redis().ping()),
      check("rag", () => ragHealth()),
    ]);

    const ok = checks.every((r) => r.ok);
    return c.json(
      {
        status: ok ? "ok" : "degraded",
        checks: Object.fromEntries(
          checks.map((r) => [
            r.name,
            r.ok ? { ok: true } : { ok: false, detail: r.detail },
          ]),
        ),
      },
      ok ? 200 : 503,
    );
  });
