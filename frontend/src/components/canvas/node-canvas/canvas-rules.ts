import type { CanvasAnyNodeType, CanvasHandleType, CanvasNode, CanvasNodeStatus } from "@/types/canvas";

/** 句柄的数据类型表，与后端 `lib/canvas/graph.py` 的 INPUT_HANDLES / OUTPUT_HANDLES 一致。 */
const INPUT_HANDLES: Partial<Record<CanvasAnyNodeType, Record<string, CanvasHandleType>>> = {
  image_generate: { prompt: "text", image1: "image" },
  video_generate: { prompt: "text", frame: "image" },
  audio_generate: { text: "text" },
  markdown: { in: "doc" },
};
const OUTPUT_HANDLES: Partial<Record<CanvasAnyNodeType, Record<string, CanvasHandleType>>> = {
  text: { text: "text" },
  image: { image: "image" },
  image_generate: { image: "image" },
  video: { video: "video" },
  audio: { audio: "audio" },
  video_generate: { video: "video" },
  audio_generate: { audio: "audio" },
  asset: { asset: "asset" },
  markdown: { out: "doc" },
};

/** 自动挂出 / 推导出的节点上，只承接来源连线的被动输入句柄。 */
export const PASSIVE_INPUT_HANDLE = "in";

export interface ConnectionEnds {
  source: string | null;
  sourceHandle: string | null;
  target: string | null;
  targetHandle: string | null;
}

export function isValidCanvasConnection(nodes: readonly CanvasNode[], c: ConnectionEnds): boolean {
  if (!c.source || !c.target || c.source === c.target) return false;
  const src = nodes.find((n) => n.id === c.source);
  const dst = nodes.find((n) => n.id === c.target);
  if (!src || !dst) return false;
  const out = OUTPUT_HANDLES[src.type]?.[c.sourceHandle ?? ""];
  const into = INPUT_HANDLES[dst.type]?.[c.targetHandle ?? ""];
  // asset 输出口除了接同类，还能接到接受图片的输入口（图当参考图，说明随图走）。
  return out !== undefined && into !== undefined && (out === into || (out === "asset" && into === "image"));
}

export function isBusy(status: CanvasNodeStatus | undefined): boolean {
  return status === "queued" || status === "running";
}

export function graphHasBusyNode(nodes: readonly CanvasNode[]): boolean {
  return nodes.some((n) => isBusy(n.data.status));
}

export const ASPECT_RATIOS = ["9:16", "16:9", "1:1", "3:4", "4:3"] as const;

/**
 * 沿连线向上游与下游各走到底，得到与 `focusId` 同一条制作链上的全部节点与连线。
 * 选中一个节点时用它高亮来历（提示词、参考图）与去向（生成物），其余变暗。
 */
export function lineageOf(
  focusId: string,
  edges: readonly { id: string; source: string; target: string }[],
  nodes: readonly { id: string; parentId?: string }[] = [],
): { nodeIds: Set<string>; edgeIds: Set<string> } {
  const nodeIds = new Set([focusId]);
  const edgeIds = new Set<string>();
  // 选中分组（角色 / 集 / 资产）：整组一起高亮，组内的连线也点亮；组外的变暗。
  const members = nodes.filter((n) => n.parentId === focusId).map((n) => n.id);
  if (members.length > 0) {
    members.forEach((id) => nodeIds.add(id));
    for (const edge of edges) {
      if (nodeIds.has(edge.source) && nodeIds.has(edge.target)) edgeIds.add(edge.id);
    }
    return { nodeIds, edgeIds };
  }
  const walk = (from: "source" | "target", to: "source" | "target") => {
    const queue = [focusId];
    const seen = new Set(queue);
    while (queue.length > 0) {
      const current = queue.shift() as string;
      for (const edge of edges) {
        if (edge[from] !== current) continue;
        edgeIds.add(edge.id);
        nodeIds.add(edge[to]);
        if (!seen.has(edge[to])) {
          seen.add(edge[to]);
          queue.push(edge[to]);
        }
      }
    }
  };
  walk("target", "source");
  walk("source", "target");
  // 链上节点所在的分组外框保持清晰，不被一并变暗。
  for (const node of nodes) {
    if (node.parentId && nodeIds.has(node.id)) nodeIds.add(node.parentId);
  }
  return { nodeIds, edgeIds };
}

/** 提示词里 `@节点id` 提到的节点，与已加入上下文的节点合并（保持先后顺序、去重）。 */
export function referencedNodeIds(text: string, contextIds: readonly string[], nodes: readonly CanvasNode[]): string[] {
  const mentioned = nodes.filter((n) => text.includes(`@${n.id}`)).map((n) => n.id);
  return [...new Set([...contextIds, ...mentioned])].filter((id) => nodes.some((n) => n.id === id));
}

const CONTEXT_DATA_LIMIT = 1500;

/** 引用的是分组时，附上组里有什么：文档节点逐个列出（含剧本审阅状态），单元节点只数个数，免得把整集塞进上下文。 */
function groupMembers(groupId: string, nodes: readonly CanvasNode[]): string {
  const members = nodes.filter((n) => n.parentId === groupId);
  const docs = members.filter((n) => n.type === "markdown");
  const units = new Set(members.map((n) => n.data.unit).filter(Boolean));
  const lines = docs.map((n) => `  - ${n.id} (markdown, step=${n.data.step ?? ""}, file=${n.data.file ?? ""}${n.data.screenplay_status ? `, screenplay_status=${n.data.screenplay_status}` : ""})`);
  lines.push(`  - ${members.length} member nodes, ${units.size} units on the canvas`);
  return `  members:\n${lines.join("\n")}`;
}

/** 发给 Agent 的说明：先列出被引用节点的 id 与当前内容，再附用户原话；Agent 据此用 canvas_* 工具直接改画布。 */
export function buildAgentNote(text: string, ids: readonly string[], nodes: readonly CanvasNode[]): string {
  if (ids.length === 0) return text;
  const lines = ids.map((id) => {
    const node = nodes.find((n) => n.id === id);
    const data = JSON.stringify(node?.data ?? {});
    const head = `- ${id} (${node?.type ?? "unknown"}): ${data.length > CONTEXT_DATA_LIMIT ? `${data.slice(0, CONTEXT_DATA_LIMIT)}…` : data}`;
    return node?.type === "group" ? `${head}\n${groupMembers(id, nodes)}` : head;
  });
  return `Referenced canvas nodes (the user's @mentions; apply the request to these nodes):\n${lines.join("\n")}\n\n${text}`;
}
