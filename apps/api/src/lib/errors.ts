import type { ContentfulStatusCode } from "hono/utils/http-status";

/**
 * Application errors carry a stable machine-readable code alongside the HTTP
 * status, so the frontend can branch on `code` rather than parsing messages.
 */
export class AppError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: ContentfulStatusCode = 400,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "AppError";
  }
}

export const badRequest = (message: string, details?: unknown): AppError =>
  new AppError("bad_request", message, 400, details);

export const unauthorized = (message = "Authentication required"): AppError =>
  new AppError("unauthorized", message, 401);

export const forbidden = (message = "Not permitted"): AppError =>
  new AppError("forbidden", message, 403);

export const notFound = (resource = "Resource"): AppError =>
  new AppError("not_found", `${resource} not found`, 404);

export const conflict = (message: string): AppError =>
  new AppError("conflict", message, 409);

export const payloadTooLarge = (message: string): AppError =>
  new AppError("payload_too_large", message, 413);

export const unprocessable = (message: string, details?: unknown): AppError =>
  new AppError("unprocessable", message, 422, details);

export const upstreamFailure = (message: string): AppError =>
  new AppError("upstream_failure", message, 502);
