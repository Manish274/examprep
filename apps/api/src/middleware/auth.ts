import type { MiddlewareHandler } from "hono";
import { bearerToken, verifyAccessToken } from "../lib/auth.js";
import { isLoginSessionLive } from "../lib/login-sessions.js";
import { unauthorized } from "../lib/errors.js";
import type { AppEnv } from "../types.js";

/**
 * Rejects the request unless it carries a valid access token, and puts the
 * user id on the context for downstream handlers.
 *
 * Ownership is enforced separately, in each query: authentication says who is
 * asking, never what they may reach.
 */
export const requireAuth = (): MiddlewareHandler<AppEnv> => async (c, next) => {
  const token = bearerToken(c.req.header("authorization"));
  if (!token) {
    throw unauthorized("Missing bearer token");
  }

  const claims = await verifyAccessToken(token);
  if (!claims) {
    throw unauthorized("Invalid or expired token");
  }

  // Checked on every request, not only at refresh: a signed-out session's
  // access token is otherwise good for the rest of its fifteen minutes, and
  // its material is already being deleted.
  if (!(await isLoginSessionLive(claims.sid))) {
    throw unauthorized("Session has ended");
  }

  c.set("userId", claims.sub);
  c.set("loginSessionId", claims.sid);
  await next();
};

/** Reads the current sign-in's id. Only valid behind requireAuth. */
export function currentLoginSessionId(c: {
  get: (k: "loginSessionId") => string | undefined;
}): string {
  const id = c.get("loginSessionId");
  if (!id) {
    throw unauthorized();
  }
  return id;
}

/** Reads the authenticated user id. Only valid behind requireAuth. */
export function currentUserId(c: { get: (k: "userId") => string | undefined }): string {
  const userId = c.get("userId");
  if (!userId) {
    throw unauthorized();
  }
  return userId;
}
