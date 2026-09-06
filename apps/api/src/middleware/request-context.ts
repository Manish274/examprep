import { randomUUID } from "node:crypto";
import type { MiddlewareHandler } from "hono";
import { logger } from "../lib/logger.js";

/**
 * Attaches a request id to every request and logs completion with duration.
 * The same id becomes the correlation id for RAG-service calls and traces.
 */
export const requestContext = (): MiddlewareHandler => async (c, next) => {
  const requestId = c.req.header("x-request-id") ?? randomUUID();
  c.set("requestId", requestId);
  c.header("x-request-id", requestId);

  const start = performance.now();
  await next();
  const durationMs = Math.round(performance.now() - start);

  logger.info(
    {
      requestId,
      method: c.req.method,
      path: c.req.path,
      status: c.res.status,
      durationMs,
    },
    "request",
  );
};
