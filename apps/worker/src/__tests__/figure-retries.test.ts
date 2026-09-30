import { describe, expect, it } from "vitest";
import { FIGURE_RETRY_DELAYS_MS, nextFigureStep } from "../lib/figure-retries.js";

describe("nextFigureStep", () => {
  it("is complete when nothing is left unread", () => {
    expect(nextFigureStep(0, false, 0)).toEqual({ kind: "complete" });
  });

  it("tries unread images again after the first delay", () => {
    // The case that used to drop four pages of a scan for good.
    expect(nextFigureStep(4, false, 0)).toEqual({
      kind: "retry",
      attempt: 1,
      delayMs: FIGURE_RETRY_DELAYS_MS[0],
    });
  });

  it("waits longer before each later try", () => {
    const delays = FIGURE_RETRY_DELAYS_MS.map((_, made) => {
      const step = nextFigureStep(4, false, made);
      return step.kind === "retry" ? step.delayMs : -1;
    });
    expect(delays).toEqual([...FIGURE_RETRY_DELAYS_MS]);
    expect([...delays].sort((a, b) => a - b)).toEqual(delays);
  });

  it("reports the images as unread once every try is spent", () => {
    expect(nextFigureStep(4, false, FIGURE_RETRY_DELAYS_MS.length)).toEqual({
      kind: "unread",
      count: 4,
    });
  });

  it("does not retry against a spent daily quota", () => {
    // Every try would be refused the same way until it resets, hours away.
    expect(nextFigureStep(3, true, 0)).toEqual({ kind: "unread", count: 3 });
  });
});
