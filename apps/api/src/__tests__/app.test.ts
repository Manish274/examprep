import { beforeAll, describe, expect, it } from "vitest";

// The app module reads env lazily, but route imports pull in the config chain,
// so a valid environment must exist before `createApp` is imported.
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

describe("app", () => {
  it("reports liveness without touching dependencies", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/health");

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({
      status: "ok",
      service: "api",
    });
  });

  it("returns a structured error for an unknown route", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/does-not-exist");

    expect(res.status).toBe(404);
    await expect(res.json()).resolves.toMatchObject({
      error: { code: "not_found" },
    });
  });

  it("echoes a supplied request id", async () => {
    const { createApp } = await import("../app.js");
    const res = await createApp().request("/health", {
      headers: { "x-request-id": "test-request-id" },
    });

    expect(res.headers.get("x-request-id")).toBe("test-request-id");
  });
});
