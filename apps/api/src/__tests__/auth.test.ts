import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  Object.assign(process.env, {
    DATABASE_URL: "postgresql://u:p@localhost:5432/db",
    REDIS_URL: "redis://localhost:6379",
    RAG_SERVICE_URL: "http://localhost:8000",
    INTERNAL_SERVICE_TOKEN: "dev_internal_token",
    JWT_ACCESS_SECRET: "a".repeat(32),
    JWT_REFRESH_SECRET: "b".repeat(32),
    ACCESS_TOKEN_TTL: "15m",
    NODE_ENV: "test",
  });
});

describe("password hashing", () => {
  it("verifies a correct password", async () => {
    const { hashPassword, verifyPassword } = await import("../lib/auth.js");
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword(hash, "correct horse battery staple")).toBe(true);
  });

  it("rejects an incorrect password", async () => {
    const { hashPassword, verifyPassword } = await import("../lib/auth.js");
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword(hash, "wrong password")).toBe(false);
  });

  it("produces a different hash each time", async () => {
    // Argon2 salts per call. Identical hashes would mean the salt is missing
    // and the whole table becomes one rainbow-table lookup.
    const { hashPassword } = await import("../lib/auth.js");
    const [a, b] = await Promise.all([
      hashPassword("same password"),
      hashPassword("same password"),
    ]);
    expect(a).not.toBe(b);
  });

  it("treats a corrupt stored hash as a failed login", async () => {
    // Must not throw: a crash here would distinguish a corrupt record from a
    // wrong password.
    const { verifyPassword } = await import("../lib/auth.js");
    expect(await verifyPassword("not-a-real-hash", "anything")).toBe(false);
  });
});

describe("access tokens", () => {
  it("round-trips its claims", async () => {
    const { signAccessToken, verifyAccessToken } = await import("../lib/auth.js");
    const token = await signAccessToken({
      sub: "user-123",
      email: "student@example.com",
      sid: "session-9",
    });
    const claims = await verifyAccessToken(token);

    expect(claims).toEqual({
      sub: "user-123",
      email: "student@example.com",
      sid: "session-9",
    });
  });

  it("rejects a token that names no sign-in", async () => {
    // Issued before sessions were tracked: it could reach nothing, so the
    // student is sent to log in again rather than shown an empty workspace
    // that silently fails.
    const { SignJWT } = await import("jose");
    const { verifyAccessToken } = await import("../lib/auth.js");
    const token = await new SignJWT({ email: "a@b.c" })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("user-1")
      .setIssuedAt()
      .setExpirationTime("5m")
      .sign(new TextEncoder().encode("a".repeat(32)));

    expect(await verifyAccessToken(token)).toBeNull();
  });

  it("rejects a tampered token", async () => {
    const { signAccessToken, verifyAccessToken } = await import("../lib/auth.js");
    const token = await signAccessToken({
      sub: "user-1",
      email: "a@b.c",
      sid: "s-1",
    });
    const tampered = `${token.slice(0, -4)}AAAA`;

    expect(await verifyAccessToken(tampered)).toBeNull();
  });

  it("rejects a garbage token without throwing", async () => {
    const { verifyAccessToken } = await import("../lib/auth.js");
    expect(await verifyAccessToken("not.a.jwt")).toBeNull();
  });

  it("rejects an expired token", async () => {
    const { verifyAccessToken } = await import("../lib/auth.js");
    const { SignJWT } = await import("jose");

    // Signed here rather than through signAccessToken because the TTL is read
    // from a cached environment, so it cannot be varied per test.
    const expired = await new SignJWT({ email: "a@b.c" })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("user-1")
      .setIssuedAt(Math.floor(Date.now() / 1000) - 7200)
      .setExpirationTime(Math.floor(Date.now() / 1000) - 3600)
      .sign(new TextEncoder().encode("a".repeat(32)));

    expect(await verifyAccessToken(expired)).toBeNull();
  });

  it("rejects a token signed with the wrong secret", async () => {
    const { verifyAccessToken } = await import("../lib/auth.js");
    const { SignJWT } = await import("jose");

    const forged = await new SignJWT({ email: "a@b.c" })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("user-1")
      .setIssuedAt()
      .setExpirationTime("15m")
      .sign(new TextEncoder().encode("z".repeat(32)));

    expect(await verifyAccessToken(forged)).toBeNull();
  });
});

describe("refresh tokens", () => {
  it("returns a token alongside the hash that gets stored", async () => {
    const { generateRefreshToken, hashRefreshToken } = await import(
      "../lib/auth.js"
    );
    const { token, tokenHash } = generateRefreshToken();

    expect(token).not.toBe(tokenHash);
    // Only the hash is persisted, so a database dump cannot be replayed as a
    // set of live sessions.
    expect(hashRefreshToken(token)).toBe(tokenHash);
    expect(tokenHash).toHaveLength(64);
  });

  it("generates a distinct token each time", async () => {
    const { generateRefreshToken } = await import("../lib/auth.js");
    expect(generateRefreshToken().token).not.toBe(generateRefreshToken().token);
  });

  it("sets an expiry in the future", async () => {
    const { refreshTokenExpiry } = await import("../lib/auth.js");
    expect(refreshTokenExpiry().getTime()).toBeGreaterThan(Date.now());
  });
});

describe("bearerToken", () => {
  it("extracts a token from a well-formed header", async () => {
    const { bearerToken } = await import("../lib/auth.js");
    expect(bearerToken("Bearer abc.def.ghi")).toBe("abc.def.ghi");
  });

  it("accepts any casing of the scheme", async () => {
    const { bearerToken } = await import("../lib/auth.js");
    expect(bearerToken("bearer abc")).toBe("abc");
  });

  it.each([undefined, "", "Basic abc", "Bearer", "justatoken"])(
    "returns null for %j",
    async (header) => {
      const { bearerToken } = await import("../lib/auth.js");
      expect(bearerToken(header)).toBeNull();
    },
  );
});
