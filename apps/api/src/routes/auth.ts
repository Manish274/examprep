import { Hono } from "hono";
import { and, eq, isNull } from "drizzle-orm";
import { z } from "zod";
import { refreshTokens, users } from "@examprep/db";
import { validate } from "../lib/validate.js";
import { db } from "../lib/db.js";
import {
  generateRefreshToken,
  hashRefreshToken,
  refreshTokenExpiry,
  signAccessToken,
} from "../lib/auth.js";
import { unauthorized } from "../lib/errors.js";
import { endLoginSession, startLoginSession } from "../lib/login-sessions.js";
import { logger } from "../lib/logger.js";
import { disconnectLoginSession } from "../ws/hub.js";
import { requireAuth, currentUserId } from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

/**
 * Starting and ending a visit.
 *
 * No accounts: a name is all a student gives, and each start is a new user
 * with an empty workspace. Two students who type the same name are two
 * different users -- a name identifies nobody, so it must not unlock anything.
 */

const startSchema = z.object({
  name: z
    .string()
    .transform((value) => value.replace(/\s+/g, " ").trim())
    .pipe(z.string().min(1, "Enter your name").max(60)),
});

const refreshSchema = z.object({
  refreshToken: z.string().min(1),
});

async function issueTokens(
  userId: string,
  loginSessionId: string,
  userAgent?: string,
) {
  const accessToken = await signAccessToken({
    sub: userId,
    sid: loginSessionId,
  });
  const { token, tokenHash } = generateRefreshToken();

  await db()
    .insert(refreshTokens)
    .values({
      userId,
      loginSessionId,
      tokenHash,
      expiresAt: refreshTokenExpiry(),
      userAgent: userAgent ?? null,
    });

  return { accessToken, refreshToken: token };
}

export const authRoutes = new Hono<AppEnv>()
  .post("/start", validate("json", startSchema), async (c) => {
    const { name } = c.req.valid("json");

    const [created] = await db()
      .insert(users)
      .values({ displayName: name })
      .returning({ id: users.id, name: users.displayName });

    if (!created) {
      throw new Error("Failed to start");
    }

    const tokens = await issueTokens(
      created.id,
      await startLoginSession(created.id),
      c.req.header("user-agent"),
    );
    return c.json(
      { user: { id: created.id, name: created.name ?? name }, ...tokens },
      201,
    );
  })

  .post("/refresh", validate("json", refreshSchema), async (c) => {
    const { refreshToken } = c.req.valid("json");
    const tokenHash = hashRefreshToken(refreshToken);

    const [stored] = await db()
      .select({
        id: refreshTokens.id,
        userId: refreshTokens.userId,
        loginSessionId: refreshTokens.loginSessionId,
        expiresAt: refreshTokens.expiresAt,
      })
      .from(refreshTokens)
      .where(
        and(
          eq(refreshTokens.tokenHash, tokenHash),
          isNull(refreshTokens.revokedAt),
        ),
      )
      .limit(1);

    if (!stored || stored.expiresAt < new Date() || !stored.loginSessionId) {
      throw unauthorized("Invalid or expired refresh token");
    }

    // Rotate: the presented token is retired as the replacement is issued, so
    // a stolen refresh token is usable at most once. The replacement is
    // written first, so the session is never without a live token for the
    // sweep to mistake for an abandoned one.
    const tokens = await issueTokens(
      stored.userId,
      stored.loginSessionId,
      c.req.header("user-agent"),
    );
    await db()
      .update(refreshTokens)
      .set({ revokedAt: new Date() })
      .where(eq(refreshTokens.id, stored.id));

    return c.json(tokens);
  })

  .post("/logout", validate("json", refreshSchema), async (c) => {
    const [stored] = await db()
      .update(refreshTokens)
      .set({ revokedAt: new Date() })
      .where(eq(refreshTokens.tokenHash, hashRefreshToken(c.req.valid("json").refreshToken)))
      .returning({ loginSessionId: refreshTokens.loginSessionId });

    // Leaving ends the visit, and its uploads and chats go with it.
    if (stored?.loginSessionId) {
      disconnectLoginSession(stored.loginSessionId);
      await endLoginSession(stored.loginSessionId).catch((err: unknown) => {
        // Already unusable; the sweep finishes removing what is left.
        logger.error({ err }, "failed to clear an ended session");
      });
    }

    // Always 204: revoking an unknown token is not an error worth reporting,
    // and saying so would confirm which tokens exist.
    return c.body(null, 204);
  })

  .get("/me", requireAuth(), async (c) => {
    const [user] = await db()
      .select({
        id: users.id,
        name: users.displayName,
        createdAt: users.createdAt,
      })
      .from(users)
      .where(eq(users.id, currentUserId(c)))
      .limit(1);

    if (!user) {
      throw unauthorized("Session has ended");
    }
    return c.json({ user });
  });
