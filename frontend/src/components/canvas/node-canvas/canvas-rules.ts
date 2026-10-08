import type { CanvasAnyNodeType, CanvasHandleType, CanvasNode, CanvasNodeStatus } from "@/types/canvas";

/** 句柄的数据类型表，与后端 `lib/canvas/graph.py` 的 INPUT_HANDLES / OUTPUT_HANDLES 一致。 */
const INPUT_HANDLES: Partial<Record<CanvasAnyNodeType, Record<string, CanvasHandleType>>> = {
  image_generate: { prompt: "text", image1: "image" },
  h3_writer: { units: "unit" },
  video_generate: { prompt: "text", frame: "image" },
  audio_generate: { text: "text" },
};
const OUTPUT_HANDLES: Partial<Record<CanvasAnyNodeType, Record<string, CanvasHandleType>>> = {
  text: { text: "text" },
  image: { image: "image" },
  image_generate: { image: "image" },
  h3_writer: { h3: "h3" },
  video: { video: "video" },
  audio: { audio: "audio" },
  video_generate: { video: "video" },
  audio_generate: { audio: "audio" },
  unit: { unit: "unit", text: "text" },
  keyframe: { image: "image" },
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
  return out !== undefined && out === into;
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
): { nodeIds: Set<string>; edgeIds: Set<string> } {
  const nodeIds = new Set([focusId]);
  const edgeIds = new Set<string>();
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
  return { nodeIds, edgeIds };
}
