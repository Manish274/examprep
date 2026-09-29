import { beforeAll, describe, expect, it, vi, afterEach } from "vitest";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Serves a canned SSE body, optionally split at arbitrary byte boundaries. */
function stubStream(pieces: string[], status = 200): void {
  const encoder = new TextEncoder();
  vi.stubGlobal("fetch", async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (const piece of pieces) controller.enqueue(encoder.encode(piece));
          controller.close();
        },
      }),
      { status },
    ),
  );
}

async function collect() {
  const { ragChatStream } = await import("../lib/rag-client.js");
  const events = [];
  for await (const event of ragChatStream({
    question: "q",
    userId: "u1",
    mode: "detailed",
  })) {
    events.push(event);
  }
  return events;
}

describe("ragChatStream", () => {
  it("parses tokens, sources and the final event", async () => {
    stubStream([
      'event: token\ndata: {"delta":"Hello "}\n\n',
      'event: token\ndata: {"delta":"world"}\n\n',
      'event: sources\ndata: {"sources":[{"marker":"S1","chunk_id":"c1"}]}\n\n',
      'event: done\ndata: {"unsupported":false,"retrieved":8,"rewritten_query":"q2"}\n\n',
    ]);

    const events = await collect();

    expect(events.map((e) => e.type)).toEqual([
      "token",
      "token",
      "sources",
      "done",
    ]);
    expect(events[0]).toMatchObject({ delta: "Hello " });
    expect(events[3]).toMatchObject({
      unsupported: false,
      retrieved: 8,
      rewrittenQuery: "q2",
    });
  });

  it("passes the answer's stages through before its tokens", async () => {
    stubStream([
      'event: stage\ndata: {"stage":"searching"}\n\n',
      'event: stage\ndata: {"stage":"writing","passages":8}\n\n',
      'event: token\ndata: {"delta":"Hi"}\n\n',
      'event: stage\ndata: {"stage":"rechecking"}\n\n',
    ]);

    const events = await collect();

    expect(events).toEqual([
      { type: "stage", stage: "searching" },
      { type: "stage", stage: "writing", passages: 8 },
      { type: "token", delta: "Hi" },
      { type: "stage", stage: "rechecking" },
    ]);
  });

  it("drops a stage it does not know rather than forwarding it", async () => {
    // The browser validates the protocol; an unknown stage would be rejected
    // there, so it never leaves the API.
    stubStream([
      'event: stage\ndata: {"stage":"pondering"}\n\n',
      'event: token\ndata: {"delta":"ok"}\n\n',
    ]);

    const events = await collect();
    expect(events).toEqual([{ type: "token", delta: "ok" }]);
  });

  it("reassembles an event split across network chunks", async () => {
    // A network read boundary lands wherever it lands. Parsing each chunk
    // independently would drop or corrupt any event straddling one.
    stubStream(['event: token\ndata: {"del', 'ta":"split"}\n\n']);

    const events = await collect();
    expect(events).toEqual([{ type: "token", delta: "split" }]);
  });

  it("handles several events arriving in one chunk", async () => {
    stubStream([
      'event: token\ndata: {"delta":"a"}\n\nevent: token\ndata: {"delta":"b"}\n\n',
    ]);

    const events = await collect();
    expect(events.map((e) => "delta" in e && e.delta)).toEqual(["a", "b"]);
  });

  it("ignores a trailing partial event rather than mangling it", async () => {
    stubStream([
      'event: token\ndata: {"delta":"complete"}\n\nevent: token\ndata: {"delta":"trunc',
    ]);

    const events = await collect();
    expect(events).toEqual([{ type: "token", delta: "complete" }]);
  });

  it("skips a frame whose data is not valid JSON", async () => {
    // One malformed frame must not abandon the rest of the answer.
    stubStream([
      "event: token\ndata: not json\n\n",
      'event: token\ndata: {"delta":"survived"}\n\n',
    ]);

    const events = await collect();
    expect(events).toEqual([{ type: "token", delta: "survived" }]);
  });

  it("surfaces an error event from the service", async () => {
    // Once the response has begun the failure cannot travel as a status code.
    stubStream(['event: error\ndata: {"message":"upstream exploded"}\n\n']);

    const events = await collect();
    expect(events).toEqual([
      { type: "error", message: "upstream exploded" },
    ]);
  });

  it("throws when the request itself fails", async () => {
    vi.stubGlobal("fetch", async () => new Response("nope", { status: 502 }));

    const { ragChatStream } = await import("../lib/rag-client.js");
    await expect(async () => {
      for await (const _ of ragChatStream({
        question: "q",
        userId: "u1",
        mode: "detailed",
      })) {
        // consume
      }
    }).rejects.toThrow(/RAG chat failed \(502\)/);
  });

  it("ignores unknown event names", async () => {
    // Forward compatibility: a new event type from the service must not break
    // an older client.
    stubStream([
      'event: heartbeat\ndata: {}\n\n',
      'event: token\ndata: {"delta":"ok"}\n\n',
    ]);

    const events = await collect();
    expect(events).toEqual([{ type: "token", delta: "ok" }]);
  });
});
