import { Hono } from "hono";
import { describe, expect, it } from "vitest";
import { errorHandler } from "../middleware/error-handler.js";

/** An app whose one route throws `err`, answered by the real error handler. */
function throwing(err: unknown) {
  const app = new Hono();
  app.onError(errorHandler);
  app.get("/", () => {
    throw err;
  });
  return app;
}

const withCode = (code: string, message = "boom") =>
  Object.assign(new Error(message), { code });

describe("errorHandler", () => {
  it("answers an unreachable database with 503, not a bare 500", async () => {
    const res = await throwing(withCode("ECONNREFUSED")).request("/");

    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: { code: string; message: string } };
    expect(body.error.code).toBe("service_unavailable");
    expect(body.error.message).toMatch(/nothing you uploaded has been lost/i);
  });

  it("finds the code inside an AggregateError, as Node reports a refused dual-stack connect", async () => {
    const err = new AggregateError([withCode("ECONNREFUSED"), withCode("ECONNREFUSED")]);
    const res = await throwing(err).request("/");
    expect(res.status).toBe(503);
  });

  it("finds the code down a cause chain", async () => {
    const err = new Error("query failed", { cause: withCode("CONNECT_TIMEOUT") });
    const res = await throwing(err).request("/");
    expect(res.status).toBe(503);
  });

  it("treats Postgres starting up as unreachable", async () => {
    const res = await throwing(withCode("57P03")).request("/");
    expect(res.status).toBe(503);
  });

  it("leaves an ordinary failure as a 500 with no detail", async () => {
    const res = await throwing(withCode("23505", "duplicate key")).request("/");

    expect(res.status).toBe(500);
    await expect(res.json()).resolves.toEqual({
      error: { code: "internal_error", message: "Internal server error" },
    });
  });
});
