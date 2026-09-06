import { describe, expect, it } from "vitest";
import { overallPercent, STAGE_WEIGHTS } from "../lib/progress.js";

describe("overallPercent", () => {
  it("maps the start of a stage to the bottom of its band", () => {
    expect(overallPercent("embedding", 0, 100)).toBe(STAGE_WEIGHTS.embedding[0]);
  });

  it("maps the end of a stage to the top of its band", () => {
    expect(overallPercent("embedding", 100, 100)).toBe(
      STAGE_WEIGHTS.embedding[1],
    );
  });

  it("interpolates within the band", () => {
    // embedding spans 40-80, so halfway through is 60.
    expect(overallPercent("embedding", 50, 100)).toBe(60);
  });

  it("never exceeds the band when current overshoots total", () => {
    expect(overallPercent("parsing", 999, 10)).toBe(STAGE_WEIGHTS.parsing[1]);
  });

  it("falls back to the band floor when total is unknown", () => {
    expect(overallPercent("indexing", 0, 0)).toBe(STAGE_WEIGHTS.indexing[0]);
  });

  it("reports completion as 100", () => {
    expect(overallPercent("completed", 1, 1)).toBe(100);
  });
});
