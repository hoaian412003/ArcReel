import type { FlowBoard, FlowCard, FlowColumn } from "@/types/flow";

export type DropOutcome =
  | { kind: "approve" }
  | { kind: "redo" }
  | { kind: "noop" }
  | { kind: "rejected"; reason: "not_draggable" | "skip" | "backward" };

/**
 * 拖拽规则：只有人工列的卡片可拖；落到相邻的下一列 = 通过，落到相邻的上一列（agent 列）= 让 Agent 重做，
 * 其余落点一律拒绝。
 */
export function resolveDrop(columns: readonly FlowColumn[], card: FlowCard, targetId: string): DropOutcome {
  const from = columns.findIndex((c) => c.id === card.column);
  const to = columns.findIndex((c) => c.id === targetId);
  if (from < 0 || to < 0) return { kind: "rejected", reason: "skip" };
  if (columns[from].kind !== "human") return { kind: "rejected", reason: "not_draggable" };
  if (from === to) return { kind: "noop" };
  if (to === from + 1) return { kind: "approve" };
  if (to === from - 1) return { kind: "redo" };
  return { kind: "rejected", reason: to < from ? "backward" : "skip" };
}

export function cardsByColumn(board: FlowBoard): Map<string, FlowCard[]> {
  const map = new Map<string, FlowCard[]>(board.columns.map((c) => [c.id, []]));
  for (const card of board.cards) map.get(card.column)?.push(card);
  for (const list of map.values()) list.sort((a, b) => a.episode - b.episode);
  return map;
}
