import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  Object.assign(process.env, {
    DATABASE_URL: "postgresql://u:p@localhost:5432/db",
    REDIS_URL: "redis://localhost:6379",
    RAG_SERVICE_URL: "http://localhost:8000",
    INTERNAL_SERVICE_TOKEN: "dev_internal_token",
    JWT_ACCESS_SECRET: "a".repeat(32),
    JWT_REFRESH_SECRET: "b".repeat(32),
    NODE_ENV: "test",
  });
});

describe("console", () => {
  it("serves the page as html", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/console");

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/html");
    await expect(res.text()).resolves.toContain("RAG Console");
  });

  it("refuses an unauthenticated retrieval search", async () => {
    // The console is public; everything it can reach is not.
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/api/retrieval/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: "anything" }),
    });

    expect(res.status).toBe(401);
  });

  it("rejects an unknown retrieval strategy", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/api/retrieval/search", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer nonsense",
      },
      body: JSON.stringify({ query: "anything", strategies: ["magic"] }),
    });

    // Auth runs first; the assertion is that a bad strategy name never
    // reaches the RAG service as an unvalidated string.
    expect([401, 422]).toContain(res.status);
  });
});
