import type { CanvasAnyNodeType } from "@/types/canvas";

export interface LayoutChild {
  id: string;
  type: CanvasAnyNodeType;
  width: number;
  height: number;
}

export interface LayoutEdge {
  source: string;
  target: string;
}

export interface GroupLayout {
  /** 子节点相对分组左上角的位置。 */
  positions: Record<string, { x: number; y: number }>;
  width: number;
  height: number;
}

/** 分组头（名称、按钮）占的高度，子节点从它下面开始排。 */
const GROUP_PADDING = "[top=60,left=28,bottom=28,right=28]";

type ElkInstance = { layout: (graph: unknown) => Promise<ElkResult> };
interface ElkResult {
  width?: number;
  height?: number;
  children?: { id: string; x?: number; y?: number }[];
}

let elkPromise: Promise<ElkInstance> | null = null;

/** elkjs 约 1 MB，按需加载，只在画布页第一次排版时下载。 */
function loadElk(): Promise<ElkInstance> {
  elkPromise ??= import("elkjs/lib/elk.bundled.js").then((mod) => new mod.default() as unknown as ElkInstance);
  return elkPromise;
}

/**
 * 用 ELK 的分层算法把一个分组里的子节点按数据流从左到右排开，尺寸取节点真实大小，所以内容再高也不会重叠；
 * 返回子节点位置与分组应有的宽高。输入顺序即同层内的先后顺序（`considerModelOrder`），布局稳定、不会来回跳。
 */
export async function layoutGroup(children: readonly LayoutChild[], edges: readonly LayoutEdge[]): Promise<GroupLayout> {
  const ids = new Set(children.map((c) => c.id));
  const elk = await loadElk();
  const result = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.padding": GROUP_PADDING,
      "elk.spacing.nodeNode": "36",
      "elk.layered.spacing.nodeNodeBetweenLayers": "72",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: children.map((c) => ({
      id: c.id,
      width: c.width,
      height: c.height,
    })),
    edges: edges
      .filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target)
      .map((e, index) => ({ id: `e${index}`, sources: [e.source], targets: [e.target] })),
  });
  const positions: Record<string, { x: number; y: number }> = {};
  for (const child of result.children ?? []) positions[child.id] = { x: child.x ?? 0, y: child.y ?? 0 };
  return { positions, width: result.width ?? 0, height: result.height ?? 0 };
}
