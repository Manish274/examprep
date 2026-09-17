import { hash, verify } from "@node-rs/argon2";
import { SignJWT, jwtVerify } from "jose";
import { createHash, randomBytes } from "node:crypto";
import { env } from "../env.js";

/**
 * Argon2id parameters. OWASP's current floor is 19 MiB and one iteration; this
 * doubles the memory, which costs a few tens of milliseconds per login and
 * meaningfully raises the cost of an offline attack on a stolen dump.
 */
const ARGON2_OPTIONS = {
  memoryCost: 39_936,
  timeCost: 2,
  parallelism: 1,
} as const;

export const hashPassword = (password: string): Promise<string> =>
  hash(password, ARGON2_OPTIONS);

export async function verifyPassword(
  storedHash: string,
  password: string,
): Promise<boolean> {
  try {
    return await verify(storedHash, password, ARGON2_OPTIONS);
  } catch {
    // A malformed hash must read as a failed login, never as a crash that
    // distinguishes "no such user" from "corrupt record".
    return false;
  }
}

export interface AccessTokenClaims {
  sub: string;
  email: string;
  /** The sign-in this token belongs to; everything it touches is scoped by it. */
  sid: string;
}

const secret = (value: string): Uint8Array => new TextEncoder().encode(value);

export async function signAccessToken(
  claims: AccessTokenClaims,
): Promise<string> {
  const { JWT_ACCESS_SECRET, ACCESS_TOKEN_TTL } = env();
  return new SignJWT({ email: claims.email, sid: claims.sid })
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
      typeof payload.email !== "string" ||
      // A token from before sign-ins were tracked names no session, and so
      // could reach nothing. Refusing it sends the student to log in again.
      typeof payload.sid !== "string"
    ) {
      return null;
    }
    return { sub: payload.sub, email: payload.email, sid: payload.sid };
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
