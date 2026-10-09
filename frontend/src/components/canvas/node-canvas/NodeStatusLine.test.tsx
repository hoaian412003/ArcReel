import { describe, expect, it } from "vitest";
import { formatAgo, formatElapsed } from "./NodeStatusLine";

describe("node status line formatting", () => {
  it("formats the time since the last update", () => {
    const base = Date.parse("2026-10-09T10:00:00Z");
    expect(formatAgo(base, base + 2_000, "en")).toBe("now");
    expect(formatAgo(base, base + 30_000, "en")).toMatch(/30/);
    expect(formatAgo(base, base + 5 * 60_000, "en")).toMatch(/5/);
    expect(formatAgo(base, base + 3 * 3_600_000, "en")).toMatch(/3/);
  });

  it("formats how long a task has been waiting", () => {
    expect(formatElapsed(9_000)).toBe("9s");
    expect(formatElapsed(65_000)).toBe("1m 05s");
  });
});
