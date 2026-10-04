import { describe, expect, it } from "vitest";
import {
  formatAgeSeconds,
  formatMoney,
  isTerminalStatus,
  laneUsagePercent,
  parseLimitInput,
  taskActions,
  taskCostLabels,
} from "./queue-format";

describe("formatAgeSeconds", () => {
  it.each([
    [0, "0s"],
    [45.9, "45s"],
    [185, "3m 05s"],
    [7800, "2h 10m"],
    [100800, "1d 4h"],
  ])("formats %s seconds as %s", (input, expected) => {
    expect(formatAgeSeconds(input)).toBe(expected);
  });

  it.each([[null], [undefined], [-1], [Number.NaN]])("returns null for %s", (input) => {
    expect(formatAgeSeconds(input)).toBeNull();
  });
});

describe("formatMoney", () => {
  it("trims trailing zeros and appends the currency", () => {
    expect(formatMoney(1.5, "USD")).toBe("1.5 USD");
    expect(formatMoney(0.12345678, "CNY")).toBe("0.1235 CNY");
  });

  it("omits the currency when absent and returns null for missing amounts", () => {
    expect(formatMoney(12)).toBe("12");
    expect(formatMoney(null, "USD")).toBeNull();
  });
});

describe("laneUsagePercent", () => {
  it("rounds, clamps to 100 and treats a non-positive limit as empty", () => {
    expect(laneUsagePercent(1, 3)).toBe(33);
    expect(laneUsagePercent(5, 2)).toBe(100);
    expect(laneUsagePercent(2, 0)).toBe(0);
  });
});

describe("taskActions / isTerminalStatus", () => {
  it("allows cancel only for queued and mark-failed only for running", () => {
    expect(taskActions("queued")).toEqual({ canCancel: true, canMarkFailed: false });
    expect(taskActions("running")).toEqual({ canCancel: false, canMarkFailed: true });
    expect(taskActions("failed")).toEqual({ canCancel: false, canMarkFailed: false });
  });

  it("classifies terminal statuses", () => {
    expect(isTerminalStatus("succeeded")).toBe(true);
    expect(isTerminalStatus("running")).toBe(false);
  });
});

describe("taskCostLabels", () => {
  it("pairs estimated and actual cost with their own currencies", () => {
    expect(
      taskCostLabels({ estimated_cost_amount: 0.5, estimated_currency: "USD", cost_amount: null, currency: null }),
    ).toEqual({ estimated: "0.5 USD", actual: null });
  });
});

describe("parseLimitInput", () => {
  it.each([
    ["", null],
    ["  ", null],
    ["4", 4],
    ["0", undefined],
    ["1001", undefined],
    ["2.5", undefined],
    ["abc", undefined],
  ])("parses %j as %s", (raw, expected) => {
    expect(parseLimitInput(raw)).toBe(expected);
  });
});
