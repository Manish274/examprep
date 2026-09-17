import { SignJWT, jwtVerify } from "jose";
import { createHash, randomBytes } from "node:crypto";
import { env } from "../env.js";

/**
 * Tokens for a visit.
 *
 * There are no passwords: a student types a name and starts. The tokens are
 * still what keeps one visitor's uploads and chats away from another's -- an
 * id in a request proves nothing, a signed token does.
 */

export interface AccessTokenClaims {
  sub: string;
  /** The visit this token belongs to; everything it touches is scoped by it. */
  sid: string;
}

const secret = (value: string): Uint8Array => new TextEncoder().encode(value);

export async function signAccessToken(
  claims: AccessTokenClaims,
): Promise<string> {
  const { JWT_ACCESS_SECRET, ACCESS_TOKEN_TTL } = env();
  return new SignJWT({ sid: claims.sid })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(claims.sub)
    .setIssuedAt()
    .setExpirationTime(ACCESS_TOKEN_TTL)
    .sign(secret(JWT_ACCESS_SECRET));
}

export async function verifyAccessToken(
  token: string,
): Promise<AccessTokenClaims | null> {
  try {
    const { payload } = await jwtVerify(token, secret(env().JWT_ACCESS_SECRET), {
      algorithms: ["HS256"],
    });
    if (
      typeof payload.sub !== "string" ||
      // A token from before visits were tracked names none, and so could
      // reach nothing. Refusing it sends the student back to the start.
      typeof payload.sid !== "string"
    ) {
      return null;
    }
    return { sub: payload.sub, sid: payload.sid };
  } catch {
    // Expired, tampered, or wrong algorithm — all are simply "not authenticated".
    return null;
  }
}

/**
 * Refresh tokens are opaque random strings, not JWTs. Only their hash is
 * stored, so a database dump cannot be replayed as a set of live sessions.
 */
export function generateRefreshToken(): { token: string; tokenHash: string } {
  const token = randomBytes(32).toString("base64url");
  return { token, tokenHash: hashRefreshToken(token) };
}

export const hashRefreshToken = (token: string): string =>
  createHash("sha256").update(token).digest("hex");

export function refreshTokenExpiry(): Date {
  const days = env().REFRESH_TOKEN_TTL_DAYS;
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000);
}

/** Extracts a bearer token from an Authorization header. */
export function bearerToken(header: string | undefined): string | null {
  if (!header) return null;
  const [scheme, token] = header.split(" ");
  if (scheme?.toLowerCase() !== "bearer" || !token) return null;
  return token;
}
