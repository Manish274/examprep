import { Hono } from "hono";
import { and, eq, isNull, sql } from "drizzle-orm";
import { z } from "zod";
import { refreshTokens, users } from "@examprep/db";
import { validate } from "../lib/validate.js";
import { db } from "../lib/db.js";
import {
  generateRefreshToken,
  hashPassword,
  hashRefreshToken,
  refreshTokenExpiry,
  signAccessToken,
  verifyPassword,
} from "../lib/auth.js";
import { conflict, unauthorized } from "../lib/errors.js";
import {
  endLoginSession,
  purgeUnscoped,
  startLoginSession,
} from "../lib/login-sessions.js";
import { logger } from "../lib/logger.js";
import { disconnectLoginSession } from "../ws/hub.js";
import { requireAuth, currentUserId } from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

const credentialsSchema = z.object({
  email: z.string().email().max(320),
  password: z.string().min(8).max(200),
  displayName: z.string().min(1).max(120).optional(),
});

const refreshSchema = z.object({
  refreshToken: z.string().min(1),
});

async function issueTokens(
  userId: string,
  email: string,
  loginSessionId: string,
  userAgent?: string,
) {
  const accessToken = await signAccessToken({
    sub: userId,
    email,
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
  .post("/register", validate("json", credentialsSchema), async (c) => {
    const { email, password, displayName } = c.req.valid("json");

    const existing = await db()
      .select({ id: users.id })
      .from(users)
      .where(sql`lower(${users.email}) = lower(${email})`)
      .limit(1);

    if (existing.length > 0) {
      throw conflict("An account with that email already exists");
    }

    const [created] = await db()
      .insert(users)
      .values({
        email,
        passwordHash: await hashPassword(password),
        displayName: displayName ?? null,
      })
      .returning({ id: users.id, email: users.email });

    if (!created) {
      throw new Error("Failed to create user");
    }

    const tokens = await issueTokens(
      created.id,
      created.email,
      await startLoginSession(created.id),
      c.req.header("user-agent"),
    );
    return c.json({ user: created, ...tokens }, 201);
  })

  .post("/login", validate("json", credentialsSchema.omit({ displayName: true })), async (c) => {
    const { email, password } = c.req.valid("json");

    const [found] = await db()
      .select({
        id: users.id,
        email: users.email,
        passwordHash: users.passwordHash,
      })
      .from(users)
      .where(sql`lower(${users.email}) = lower(${email})`)
      .limit(1);

    // Hash even when no user matched, so response time does not reveal
    // whether an email is registered.
    const ok = found
      ? await verifyPassword(found.passwordHash, password)
      : await verifyPassword("$argon2id$v=19$m=39936,t=2,p=1$AAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", password);

    if (!found || !ok) {
      throw unauthorized("Incorrect email or password");
    }

    // Every login is a fresh start. Material from before sessions were
    // tracked has nothing to expire with, so it goes now. Other sign-ins that
    // are still live -- another device -- keep theirs until they end.
    await purgeUnscoped(found.id).catch((err: unknown) => {
      logger.error({ err, userId: found.id }, "failed to clear old material");
    });

    const tokens = await issueTokens(
      found.id,
      found.email,
      await startLoginSession(found.id),
      c.req.header("user-agent"),
    );
    return c.json({
      user: { id: found.id, email: found.email },
      ...tokens,
    });
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

    const [user] = await db()
      .select({ id: users.id, email: users.email })
      .from(users)
      .where(eq(users.id, stored.userId))
      .limit(1);

    if (!user) {
      throw unauthorized("Account no longer exists");
    }

    // Rotate: the presented token is retired as the replacement is issued, so
    // a stolen refresh token is usable at most once. The replacement is
    // written first, so the session is never without a live token for the
    // sweep to mistake for an abandoned one.
    const tokens = await issueTokens(
      user.id,
      user.email,
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

    // Signing out ends the session, and its uploads and chats go with it.
    if (stored?.loginSessionId) {
      disconnectLoginSession(stored.loginSessionId);
      await endLoginSession(stored.loginSessionId).catch((err: unknown) => {
        // Already unusable; the sweep finishes removing what is left.
        logger.error({ err }, "failed to clear a signed-out session");
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
        email: users.email,
        displayName: users.displayName,
        createdAt: users.createdAt,
      })
      .from(users)
      .where(eq(users.id, currentUserId(c)))
      .limit(1);

    if (!user) {
      throw unauthorized("Account no longer exists");
    }
    return c.json({ user });
  });
