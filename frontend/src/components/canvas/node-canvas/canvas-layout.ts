import type { CanvasEdge, CanvasNode } from "@/types/canvas";

/** 节点实际量到的大小（React Flow 量过之后才有）；没量到的按这个默认值排，与早先的固定间距一致。 */
export type NodeSizes = ReadonlyMap<string, { width: number; height: number }>;
const FALLBACK_SIZE = { width: 360, height: 320 };
const LAYER_GAP = 80;
const ROW_GAP = 40;

function sizeOf(sizes: NodeSizes | undefined, id: string): { width: number; height: number } {
  return sizes?.get(id) ?? FALLBACK_SIZE;
}

/**
 * 把落盘节点按数据流排成从左到右的层：没有上游的在最左列，每个节点落在「上游最深一层 + 1」。
 * 只排用户自己的节点（项目推导的集分组有自己的布局），整体保持在原来的左上角附近。环路上的边被忽略。
 * 返回 `节点 id → 新位置`。
 */
export function tidyLayout(
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
  sizes?: NodeSizes,
): Record<string, { x: number; y: number }> {
  const movable = nodes.filter((n) => !n.parentId);
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
  // 先按层分好（同一层内保持原来的上下顺序，布局前后不会让节点来回跳），再按量到的大小排：
  // 列宽取该层最宽的节点，行高取各自的高度，不会因为节点变高而互相压住。
  const layers = new Map<number, CanvasNode[]>();
  for (const node of [...movable].sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x)) {
    const layer = depth.get(node.id) ?? 0;
    layers.set(layer, [...(layers.get(layer) ?? []), node]);
  }
  const positions: Record<string, { x: number; y: number }> = {};
  let x = originX;
  for (const layer of [...layers.keys()].sort((a, b) => a - b)) {
    let y = originY;
    let widest = 0;
    for (const node of layers.get(layer) ?? []) {
      const size = sizeOf(sizes, node.id);
      positions[node.id] = { x, y };
      y += size.height + ROW_GAP;
      widest = Math.max(widest, size.width);
    }
    x += widest + LAYER_GAP;
  }
  return positions;
}


const EPISODE_STEPS = ["script", "direction", "keyframe", "h3", "video_gen", "video", "dub_text", "voice_gen", "voice"] as const;
const DOC_STEPS = ["source", "screenplay", "plan"] as const;
const PAD = 28;
const DOC_TOP = 64;
const COLUMN_SPACING = 30;
const ROW_SPACING = 24;

/**
 * 集分组（文档行 + 每个单元一行的矩阵 + 左侧关键帧板）按各节点量到的大小重排：行高取该行最高的节点，
 * 列宽取该列最宽的节点，关键帧板对齐它第一个单元所在的行。位置相对分组左上角；同时给出分组需要的大小。
 */
export function tidyEpisodeGroup(
  members: readonly CanvasNode[],
  sizes: NodeSizes,
): { positions: Record<string, { x: number; y: number }>; width: number; height: number } {
  const positions: Record<string, { x: number; y: number }> = {};
  const dims = (n: CanvasNode) => sizeOf(sizes, n.id);

  const docs = DOC_STEPS.flatMap((step) => members.filter((n) => n.type === "markdown" && n.data.step === step));
  let right = PAD;
  let docBottom = DOC_TOP;
  for (const doc of docs) {
    positions[doc.id] = { x: right, y: DOC_TOP };
    right += dims(doc).width + COLUMN_SPACING;
    docBottom = Math.max(docBottom, DOC_TOP + dims(doc).height);
  }

  const units = new Map<string, CanvasNode[]>();
  for (const n of members) {
    const unit = n.data.unit;
    if (unit && (EPISODE_STEPS as readonly string[]).includes(n.data.step ?? "")) units.set(unit, [...(units.get(unit) ?? []), n]);
  }
  // 单元按编号排（E2U02 在 E2U10 前，E2U08a 在 E2U08 后），不依赖节点当前的位置：位置可能已经被打乱。
  const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });
  const unitOrder = [...units.keys()].sort(byName);
  const boards = members.filter((n) => n.data.step === "kf_gen" || n.data.step === "kf_sheet");

  const boardWidth = boards.length > 0 ? Math.max(...boards.map((n) => dims(n).width)) : 0;
  const laneX = boards.length > 0 ? PAD + boardWidth + 40 : PAD;
  const columnWidth = new Map<string, number>();
  for (const nodes of units.values()) {
    for (const n of nodes) {
      const step = n.data.step ?? "";
      columnWidth.set(step, Math.max(columnWidth.get(step) ?? 0, dims(n).width));
    }
  }
  const columnX = new Map<string, number>();
  let cursor = laneX;
  for (const step of EPISODE_STEPS) {
    columnX.set(step, cursor);
    cursor += (columnWidth.get(step) ?? 0) + COLUMN_SPACING;
  }

  const laneTop = docBottom + 90; // 文档预览异步载入后会比量到时更高，多留一些
  const rowY = new Map<string, number>();
  let y = laneTop;
  for (const unit of unitOrder) {
    rowY.set(unit, y);
    for (const n of units.get(unit) ?? []) positions[n.id] = { x: columnX.get(n.data.step ?? "") ?? laneX, y };
    y += Math.max(...(units.get(unit) ?? []).map((n) => dims(n).height)) + ROW_SPACING;
  }
  let bottom = Math.max(y, docBottom);

  // 关键帧板：对齐第一个用到它的单元所在的行（单元的关键帧节点带着它的联合图编号），再顺着往下避开上一张。
  const sheets = new Map<string, CanvasNode[]>();
  for (const n of boards) sheets.set(n.data.sheet ?? n.id, [...(sheets.get(n.data.sheet ?? n.id) ?? []), n]);
  const firstRowOfSheet = new Map<string, number>();
  unitOrder.forEach((unit, index) => {
    for (const n of units.get(unit) ?? []) {
      if (n.data.step === "keyframe" && n.data.sheet && !firstRowOfSheet.has(n.data.sheet)) firstRowOfSheet.set(n.data.sheet, index);
    }
  });
  let previousBottom = laneTop;
  for (const sheetId of [...sheets.keys()].sort(byName)) {
    const pair = sheets.get(sheetId) ?? [];
    const row = firstRowOfSheet.get(sheetId);
    let at = Math.max(row === undefined ? laneTop : (rowY.get(unitOrder[row] ?? "") ?? laneTop), previousBottom);
    for (const n of [...pair].sort((a, b) => (a.data.step === "kf_gen" ? -1 : 1) - (b.data.step === "kf_gen" ? -1 : 1))) {
      positions[n.id] = { x: PAD, y: at };
      at += dims(n).height + 16;
    }
    previousBottom = at + ROW_SPACING;
    bottom = Math.max(bottom, previousBottom);
  }

  return { positions, width: Math.max(right, cursor) + PAD, height: bottom + 40 };
}
