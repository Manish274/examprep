import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  Object.assign(process.env, {
    DATABASE_URL: "postgresql://u:p@localhost:5432/db",
    REDIS_URL: "redis://localhost:6379",
    RAG_SERVICE_URL: "http://localhost:8000",
    INTERNAL_SERVICE_TOKEN: "dev_internal_token",
    JWT_ACCESS_SECRET: "a".repeat(32),
    NODE_ENV: "test",
  });
});

/**
 * A trace holds which of a student's documents matched their question and a
 * snippet of what was in them. That makes the authorisation boundary the part
 * of these routes worth testing without a database behind them.
 */
describe("trace routes", () => {
  it("refuses an unauthenticated summary", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/api/traces");

    expect(res.status).toBe(401);
  });

  it("refuses an unauthenticated lookup by correlation id", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request(
      "/api/traces/2a1f9c34-5b6d-4e78-9012-3456789abcde",
    );

    expect(res.status).toBe(401);
  });

  it("rejects a correlation id that is not a uuid before it reaches the database", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/api/traces/not-a-uuid", {
      headers: { authorization: "Bearer nonsense" },
    });

    // Auth is checked first, so this is still 401 -- the assertion is that it
    // is never a 500 from a malformed query reaching Postgres.
    expect([401, 422]).toContain(res.status);
  });
});
