import { describe, expect, it } from "vitest";
import type { FlowCard, FlowColumn } from "@/types/flow";
import { cardsByColumn, resolveDrop } from "./flow-rules";

const COLUMNS: FlowColumn[] = [
  { id: "script_plan", kind: "agent" },
  { id: "script_plan_review", kind: "human" },
  { id: "direction", kind: "agent" },
  { id: "direction_review", kind: "human" },
  { id: "prompts", kind: "agent" },
];

const card = (column: string, episode = 1): FlowCard => ({ episode, title: "t", column, next_action: "", blocked: false });

describe("resolveDrop", () => {
  it("人工列落到下一列为通过", () => {
    expect(resolveDrop(COLUMNS, card("script_plan_review"), "direction")).toEqual({ kind: "approve" });
    expect(resolveDrop(COLUMNS, card("direction_review"), "prompts")).toEqual({ kind: "approve" });
  });

  it("人工列落到上一列为重做", () => {
    expect(resolveDrop(COLUMNS, card("direction_review"), "direction")).toEqual({ kind: "redo" });
  });

  it("落回原列无动作", () => {
    expect(resolveDrop(COLUMNS, card("direction_review"), "direction_review")).toEqual({ kind: "noop" });
  });

  it("跨列与更早的列被拒绝", () => {
    expect(resolveDrop(COLUMNS, card("script_plan_review"), "prompts")).toEqual({ kind: "rejected", reason: "skip" });
    expect(resolveDrop(COLUMNS, card("direction_review"), "script_plan")).toEqual({
      kind: "rejected",
      reason: "backward",
    });
  });

  it("agent 列的卡片不可拖", () => {
    expect(resolveDrop(COLUMNS, card("direction"), "direction_review")).toEqual({
      kind: "rejected",
      reason: "not_draggable",
    });
  });
});

describe("cardsByColumn", () => {
  it("按列分组并按集号排序，空列保留", () => {
    const grouped = cardsByColumn({ columns: COLUMNS, cards: [card("direction", 3), card("direction", 2)] });
    expect(grouped.get("direction")?.map((c) => c.episode)).toEqual([2, 3]);
    expect(grouped.get("prompts")).toEqual([]);
  });
});
