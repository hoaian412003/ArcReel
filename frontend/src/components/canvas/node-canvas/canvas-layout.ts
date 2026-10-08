import type { CanvasEdge, CanvasNode } from "@/types/canvas";

const COLUMN_GAP = 440;
const ROW_GAP = 360;

/**
 * 把落盘节点按数据流排成从左到右的层：没有上游的在最左列，每个节点落在「上游最深一层 + 1」。
 * 只排用户自己的节点（项目推导的集分组有自己的布局），整体保持在原来的左上角附近。环路上的边被忽略。
 * 返回 `节点 id → 新位置`。
 */
export function tidyLayout(
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
): Record<string, { x: number; y: number }> {
  const movable = nodes.filter((n) => !n.derived);
  if (movable.length === 0) return {};
  const ids = new Set(movable.map((n) => n.id));
  const incoming = new Map<string, string[]>(movable.map((n) => [n.id, []]));
  for (const edge of edges) {
    if (ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target) {
      incoming.get(edge.target)?.push(edge.source);
    }
  }

  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const value = Math.max(-1, ...(incoming.get(id) ?? []).map(depthOf)) + 1;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };
  for (const node of movable) depthOf(node.id);

  const originX = Math.min(...movable.map((n) => n.position.x));
  const originY = Math.min(...movable.map((n) => n.position.y));
  const rows = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  // 同一层内保持原来的上下顺序，布局前后不会让节点来回跳。
  for (const node of [...movable].sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x)) {
    const layer = depth.get(node.id) ?? 0;
    const row = rows.get(layer) ?? 0;
    rows.set(layer, row + 1);
    positions[node.id] = { x: originX + layer * COLUMN_GAP, y: originY + row * ROW_GAP };
  }
  return positions;
}
