import { describe, expect, it } from "vitest";
import { loadEnv } from "../env.js";

const valid = {
  DATABASE_URL: "postgresql://u:p@localhost:5432/db",
  REDIS_URL: "redis://localhost:6379",
  RAG_SERVICE_URL: "http://localhost:8000",
  INTERNAL_SERVICE_TOKEN: "dev_internal_token",
  JWT_ACCESS_SECRET: "a".repeat(32),
  JWT_REFRESH_SECRET: "b".repeat(32),
};

describe("loadEnv", () => {
  it("accepts a valid environment and applies defaults", () => {
    const env = loadEnv(valid as NodeJS.ProcessEnv);
    expect(env.API_PORT).toBe(3001);
    expect(env.NODE_ENV).toBe("development");
    expect(env.MAX_UPLOAD_BYTES).toBe(52_428_800);
  });

  it("coerces numeric strings", () => {
    const env = loadEnv({ ...valid, API_PORT: "4000" } as NodeJS.ProcessEnv);
    expect(env.API_PORT).toBe(4000);
  });

  it("rejects a short JWT secret rather than starting insecurely", () => {
    expect(() =>
      loadEnv({ ...valid, JWT_ACCESS_SECRET: "tooshort" } as NodeJS.ProcessEnv),
    ).toThrow(/JWT_ACCESS_SECRET/);
  });

  it("rejects a missing required variable", () => {
    const { DATABASE_URL: _omitted, ...rest } = valid;
    expect(() => loadEnv(rest as NodeJS.ProcessEnv)).toThrow(/DATABASE_URL/);
  });
});
