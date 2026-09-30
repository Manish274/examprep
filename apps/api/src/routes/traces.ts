import { Hono } from "hono";
import { and, asc, desc, eq, gte, sql } from "drizzle-orm";
import { z } from "zod";
import { traces } from "@examprep/db";
import { db } from "../lib/db.js";
import { validate } from "../lib/validate.js";
import { notFound } from "../lib/errors.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

/**
 * Reading the trace table back.
 *
 * The RAG service writes spans, and these routes read them back, so the
 * answer to "why did it cite that passage?" is available to the application
 * and the dev console, not only to whoever is sitting at a psql prompt.
 *
 * Every query is scoped by owner. A correlation id is a message id, so an
 * unscoped lookup would hand one student the retrieval trace of another's
 * conversation: which of their documents matched, and a snippet of what was
 * in them.
 */

const correlationParamSchema = z.object({ id: z.string().uuid() });

const summaryQuerySchema = z.object({
  hours: z.coerce.number().int().min(1).max(720).default(24),
  kind: z.string().max(64).optional(),
});

export const traceRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  /**
   * Latency and failures by stage.
   *
   * Percentiles rather than averages: one call that waited out a rate limit
   * drags a mean somewhere no request actually was, and hides the shape of
   * everything else.
   */
  .get("/", validate("query", summaryQuerySchema), async (c) => {
    const { hours, kind } = c.req.valid("query");
    const since = new Date(Date.now() - hours * 3_600_000);

    const rows = await db()
      .select({
        kind: traces.kind,
        name: traces.name,
        calls: sql<number>`count(*)::int`,
        errors: sql<number>`count(*) FILTER (WHERE ${traces.error} IS NOT NULL)::int`,
        p50: sql<
          number | null
        >`percentile_disc(0.5) WITHIN GROUP (ORDER BY ${traces.durationMs})`,
        p95: sql<
          number | null
        >`percentile_disc(0.95) WITHIN GROUP (ORDER BY ${traces.durationMs})`,
      })
      .from(traces)
      .where(
        and(
          eq(traces.userId, currentUserId(c)),
          gte(traces.createdAt, since),
          ...(kind ? [eq(traces.kind, kind)] : []),
        ),
      )
      .groupBy(traces.kind, traces.name)
      .orderBy(desc(sql`count(*)`));

    return c.json({ since: since.toISOString(), stages: rows });
  })

  /**
   * One operation, span by span.
   *
   * The correlation id is the id of the thing produced: the assistant
   * message, so this is the direct answer to "what happened when that answer
   * was produced" -- what the question was rewritten to, what came back and
   * in what order, what the reranker moved, and which sources the model
   * actually cited. Likewise the document for an ingest, the test or
   * flashcard set for a generation, and the attempt for a grading.
   */
  .get("/:id", validate("param", correlationParamSchema), async (c) => {
    const correlationId = c.req.valid("param").id;

    const rows = await db()
      .select({
        id: traces.id,
        kind: traces.kind,
        name: traces.name,
        input: traces.input,
        output: traces.output,
        metadata: traces.metadata,
        durationMs: traces.durationMs,
        error: traces.error,
        createdAt: traces.createdAt,
      })
      .from(traces)
      .where(
        and(
          eq(traces.correlationId, correlationId),
          eq(traces.userId, currentUserId(c)),
        ),
      )
      .orderBy(asc(traces.createdAt));

    if (rows.length === 0) {
      // Indistinguishable from "belongs to someone else", deliberately: a
      // different message for a trace that exists would confirm it exists.
      throw notFound("Trace");
    }

    // Not the sum of the spans: the "request" span encloses the others, so
    // adding them together would report roughly double the real time.
    const request = rows.find((r) => r.name === "request");

    return c.json({
      correlationId,
      kind: rows[0]?.kind,
      spans: rows,
      totalMs:
        request?.durationMs ??
        Math.max(0, ...rows.map((r) => r.durationMs ?? 0)),
      failed: rows.some((r) => r.error !== null),
    });
  });
