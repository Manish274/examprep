import type { Context } from "hono";
import { HTTPException } from "hono/http-exception";
import { ZodError } from "zod";
import { AppError } from "../lib/errors.js";
import { logger } from "../lib/logger.js";

/**
 * Codes meaning a dependency -- Postgres, Redis, the RAG service -- could not
 * be reached at all, as opposed to a request it rejected: Node's socket
 * errors, postgres.js's connection errors, and Postgres shutting down or
 * starting up.
 */
const UNREACHABLE = new Set([
  "ECONNREFUSED",
  "ECONNRESET",
  "ETIMEDOUT",
  "ENOTFOUND",
  "EAI_AGAIN",
  "EHOSTUNREACH",
  "EPIPE",
  "CONNECT_TIMEOUT",
  "CONNECTION_CLOSED",
  "CONNECTION_ENDED",
  "CONNECTION_DESTROYED",
  "57P01",
  "57P02",
  "57P03",
]);

/** Looks through `cause` chains and AggregateErrors, where Node hides the code. */
function isUnreachable(err: unknown, depth = 0): boolean {
  if (!err || typeof err !== "object" || depth > 4) return false;
  const { code, cause, errors } = err as {
    code?: unknown;
    cause?: unknown;
    errors?: unknown;
  };
  if (typeof code === "string" && UNREACHABLE.has(code)) return true;
  if (Array.isArray(errors) && errors.some((e) => isUnreachable(e, depth + 1))) {
    return true;
  }
  return isUnreachable(cause, depth + 1);
}

/**
 * Single funnel for every error leaving the API. Guarantees the shape of an
 * error response and keeps internal detail out of production payloads.
 */
export function errorHandler(err: Error, c: Context): Response {
  if (err instanceof AppError) {
    return c.json(
      {
        error: {
          code: err.code,
          message: err.message,
          ...(err.details !== undefined ? { details: err.details } : {}),
        },
      },
      err.status,
    );
  }

  if (err instanceof ZodError) {
    return c.json(
      {
        error: {
          code: "validation_failed",
          message: "Request failed validation",
          details: err.issues,
        },
      },
      422,
    );
  }

  if (err instanceof HTTPException) {
    return c.json(
      { error: { code: "http_error", message: err.message } },
      err.status,
    );
  }

  if (isUnreachable(err)) {
    // Said plainly, because the alternative reads as data loss: an empty
    // document list beside "Internal server error", while every upload is
    // sitting safely in a database that is only restarting.
    logger.error({ err }, "dependency unreachable");
    return c.json(
      {
        error: {
          code: "service_unavailable",
          message:
            "The server can't reach one of its services right now. Nothing you uploaded has been lost — try again in a moment.",
        },
      },
      503,
    );
  }

  logger.error({ err }, "unhandled error");
  return c.json(
    { error: { code: "internal_error", message: "Internal server error" } },
    500,
  );
}
