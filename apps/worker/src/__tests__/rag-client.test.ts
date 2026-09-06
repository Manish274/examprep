import { describe, expect, it } from "vitest";
import { describeFailure } from "../lib/rag-client.js";

describe("describeFailure", () => {
  it("unwraps a FastAPI detail message", () => {
    // This string lands on the document row as the reason the upload failed,
    // so it has to read as an explanation, not as a JSON envelope.
    const body = JSON.stringify({
      detail:
        "No extractable text found. If this is a scanned document, it needs " +
        "OCR or a searchable copy before it can be studied.",
    });

    expect(describeFailure(422, body)).toBe(
      "No extractable text found. If this is a scanned document, it needs " +
        "OCR or a searchable copy before it can be studied.",
    );
  });

  it("falls back to the raw body when the response is not JSON", () => {
    const message = describeFailure(502, "<html>Bad Gateway</html>");

    expect(message).toContain("502");
    expect(message).toContain("Bad Gateway");
  });

  it("falls back when JSON carries no detail field", () => {
    const message = describeFailure(500, JSON.stringify({ error: "boom" }));
    expect(message).toContain("500");
  });

  it("ignores a non-string detail", () => {
    const message = describeFailure(400, JSON.stringify({ detail: { a: 1 } }));
    expect(message).toContain("400");
  });

  it("truncates a very long body", () => {
    const message = describeFailure(500, "x".repeat(5000));
    expect(message.length).toBeLessThan(400);
  });

  it("handles an empty body", () => {
    expect(describeFailure(503, "")).toContain("503");
  });
});
