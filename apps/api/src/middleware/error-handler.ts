import type { Context } from "hono";
import { HTTPException } from "hono/http-exception";
import { ZodError } from "zod";
import { AppError } from "../lib/errors.js";
import { logger } from "../lib/logger.js";

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

  logger.error({ err }, "unhandled error");
  return c.json(
    { error: { code: "internal_error", message: "Internal server error" } },
    500,
  );
}
