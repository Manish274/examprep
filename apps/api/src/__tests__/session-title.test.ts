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

/** The sidebar shows one line per conversation; an untitled row is useless. */
describe("session titles", () => {
  it("uses a short question as the title unchanged", async () => {
    const { deriveTitle } = await import("../ws/hub.js");
    expect(deriveTitle("what is write ahead logging")).toBe(
      "what is write ahead logging",
    );
  });

  it("cuts a long question on a word boundary", async () => {
    const { deriveTitle } = await import("../ws/hub.js");
    const title = deriveTitle(
      "how do I handle words that never appeared in my training corpus at all",
    );

    // A title cut mid-word reads as a bug rather than as an abbreviation.
    expect(title.endsWith("…")).toBe(true);
    expect(title.length).toBeLessThanOrEqual(61);
    expect(title.replace("…", "").trimEnd()).toMatch(/\w$/);
    expect(
      "how do I handle words that never appeared in my training corpus at all",
    ).toContain(title.replace("…", ""));
  });

  it("falls back rather than titling a session with whitespace", async () => {
    const { deriveTitle } = await import("../ws/hub.js");
    expect(deriveTitle("   ")).toBe("New chat");
  });

  it("collapses newlines so the sidebar stays one line", async () => {
    const { deriveTitle } = await import("../ws/hub.js");
    expect(deriveTitle("explain\n\n  smoothing")).toBe("explain smoothing");
  });

  it("hard-cuts a single unbroken word rather than returning almost nothing", async () => {
    const { deriveTitle } = await import("../ws/hub.js");
    const title = deriveTitle("x".repeat(200));
    expect(title.length).toBeGreaterThan(50);
  });
});
