import { Hono } from "hono";
import { z } from "zod";
import { validate } from "../lib/validate.js";
import { ragRetrieve, type RagRetrievedChunk } from "../lib/rag-client.js";
import {
  currentLoginSessionId,
  currentUserId,
  requireAuth,
} from "../middleware/auth.js";
import {
  filterSessionDocuments,
  sessionDocumentIds,
} from "../lib/login-sessions.js";
import type { AppEnv } from "../types.js";

/**
 * Retrieval, exposed to a signed-in user.
 *
 * The Python service's `/retrieve` is guarded by the internal service token and
 * takes a user id as a plain parameter -- fine between two backends, useless to
 * a browser, which must never hold that token nor be trusted to say who it is.
 * This route supplies the user id from the verified access token instead.
 *
 * It exists to make retrieval inspectable on its own, separately from an
 * answer. When a reply is wrong the first question is always whether the right
 * passage was retrieved at all, and a chat transcript cannot answer that.
 */

const searchSchema = z.object({
  query: z.string().min(1).max(4000),
  documentIds: z.array(z.string().uuid()).optional(),
  topK: z.coerce.number().int().min(1).max(50).default(10),
  /**
   * Several at once, because a single strategy's scores mean very little in
   * isolation. Running them side by side on one query is the comparison that
   * says whether fusion or reranking is earning its keep on this material.
   */
  strategies: z
    .array(z.enum(["bm25", "dense", "hybrid", "hybrid_rerank"]))
    .min(1)
    .max(4)
    .default(["hybrid_rerank"]),
});

interface StrategyOutcome {
  strategy: string;
  ok: boolean;
  tookMs: number;
  count: number;
  results: RagRetrievedChunk[];
  error?: string;
}

export const retrievalRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  .post("/search", validate("json", searchSchema), async (c) => {
    const userId = currentUserId(c);
    const { query, documentIds, topK, strategies } = c.req.valid("json");
    const loginSessionId = currentLoginSessionId(c);

    // The service scopes by user alone. Naming the documents is what keeps a
    // search inside this sign-in's uploads.
    const scope = documentIds
      ? await filterSessionDocuments(loginSessionId, documentIds)
      : await sessionDocumentIds(loginSessionId);
    if (scope.length === 0) {
      return c.json({ query, topK, strategies: [] });
    }

    // Sequential rather than concurrent: every strategy but bm25 makes a
    // rate-limited embedding call, and four at once against a free tier is a
    // reliable way to measure the rate limiter instead of the retriever.
    const outcomes: StrategyOutcome[] = [];
    for (const strategy of strategies) {
      const started = Date.now();
      try {
        const result = await ragRetrieve({
          query,
          userId,
          strategy,
          documentIds: scope,
          topK,
        });
        outcomes.push({
          strategy,
          ok: true,
          tookMs: result.took_ms,
          count: result.count,
          results: result.results,
        });
      } catch (err) {
        // One strategy failing must not lose the others. A comparison missing
        // its slowest member is still worth reading; an error page is not.
        outcomes.push({
          strategy,
          ok: false,
          tookMs: Date.now() - started,
          count: 0,
          results: [],
          error: err instanceof Error ? err.message.slice(0, 300) : String(err),
        });
      }
    }

    return c.json({ query, topK, strategies: outcomes });
  });
