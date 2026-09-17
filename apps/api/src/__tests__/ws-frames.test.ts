import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// The handshake checks the sign-in is still live, which is a database read.
vi.mock("../lib/login-sessions.js", () => ({
  isLoginSessionLive: async () => true,
  sessionDocumentIds: async () => [],
}));

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

/** Collects what the server writes back, standing in for a browser socket. */
function fakeSocket() {
  const sent: Record<string, unknown>[] = [];
  return {
    sent,
    ctx: {
      send: (data: string) => sent.push(JSON.parse(data) as Record<string, unknown>),
      close: () => undefined,
      readyState: 1,
    },
  };
}

afterEach(async () => {
  const { closeHub } = await import("../ws/hub.js");
  await closeHub();
});

/**
 * Nothing awaits one onMessage before the next fires, so frames sent back to
 * back are dispatched concurrently unless the connection serialises them.
 * A client that sends auth and its first question together is the ordinary
 * case, not an abusive one.
 */
describe("websocket frame ordering", () => {
  it("handles a frame sent immediately after auth, without waiting for ready", async () => {
    const { createHandlers } = await import("../ws/hub.js");
    const { signAccessToken } = await import("../lib/auth.js");

    const token = await signAccessToken({
      sub: "2a1f9c34-5b6d-4e78-9012-3456789abcde",
      email: "student@example.test",
      sid: "5c3e1d2a-7b8c-4d9e-8f01-23456789abcd",
    });

    const socket = fakeSocket();
    const handlers = createHandlers();
    handlers.onOpen(new Event("open"), socket.ctx as never);

    // Deliberately not awaited in sequence: both frames go out together.
    await Promise.all([
      handlers.onMessage(
        { data: JSON.stringify({ type: "auth", token }) } as MessageEvent,
        socket.ctx as never,
      ),
      handlers.onMessage(
        {
          data: JSON.stringify({
            // A frame that requires a completed handshake, so this fails loudly
            // if it overtakes the token check rather than passing vacuously.
            type: "subscribe:document",
            documentId: "8f14e45f-ceea-4c2a-9c1e-1b2a3c4d5e6f",
          }),
        } as MessageEvent,
        socket.ctx as never,
      ),
    ]);

    const types = socket.sent.map((frame) => frame.type);
    expect(types).toEqual(["ready"]);
    // The exact failure this guards against: "Send an auth frame first",
    // returned for a frame the client sent after its auth frame.
    expect(types).not.toContain("error");
  });

  it("rejects a client that floods rather than queueing without limit", async () => {
    const { createHandlers } = await import("../ws/hub.js");

    const socket = fakeSocket();
    const handlers = createHandlers();
    handlers.onOpen(new Event("open"), socket.ctx as never);

    await Promise.all(
      Array.from({ length: 40 }, () =>
        handlers.onMessage(
          { data: JSON.stringify({ type: "ping" }) } as MessageEvent,
          socket.ctx as never,
        ),
      ),
    );

    const codes = socket.sent
      .filter((frame) => frame.type === "error")
      .map((frame) => frame.code);
    expect(codes).toContain("too_many_frames");
  });
});
