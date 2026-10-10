import { createContext, useContext } from "react";

export interface ImageModelChoices {
  /** `供应商/模型` 候选。 */
  options: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

export interface CanvasActions {
  projectName: string;
  setText: (nodeId: string, text: string) => Promise<void>;
  /** 图片节点的参考说明（含项目资产图）；空串恢复默认 / 清除。 */
  setNote: (nodeId: string, note: string) => Promise<void>;
  setAspectRatio: (nodeId: string, aspectRatio: string) => Promise<void>;
  /** 通用字段补丁（时长、音色等供应商节点参数）。 */
  setData: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
  /** 空串恢复项目默认。 */
  setModel: (nodeId: string, model: string) => Promise<void>;
  imageModels: ImageModelChoices;
  /** 分组框缩放结束：保存新的位置与大小。 */
  resizeGroup: (nodeId: string, position: { x: number; y: number }, width: number, height: number) => Promise<void>;
  /** 按组内节点当前的实际大小重排这个分组（用于文字变长、图片载入后节点互相压住时）。 */
  tidyGroup: (groupId: string) => Promise<void>;
  /** 把图片填进（或换掉）这个图片节点。 */
  uploadImage: (nodeId: string, file: File) => Promise<void>;
  /** 把单元文本 / 导演说明存回剧本（依赖它的 H3 提示词与产出会按规则过期）。 */
  saveUnitField: (episode: number, unitId: string, field: "text" | "direction", value: string) => Promise<void>;
  /** 打开文档节点的全文。 */
  openMarkdown: (file: string, title: string) => void;
  run: (nodeId: string) => Promise<void>;
  /** 把节点加入 Agent 对话的上下文（输入框里也可以用 `@节点id` 引用）。 */
  addToContext: (nodeId: string) => void;
  remove: (nodeId: string) => Promise<void>;
  /** 复制资产节点：副本是独立资产节点，可摆到任何地方再连到图片参考口。 */
  duplicate: (nodeId: string) => Promise<void>;
}

export const CanvasActionsContext = createContext<CanvasActions | null>(null);

export function useCanvasActions(): CanvasActions {
  const actions = useContext(CanvasActionsContext);
  if (!actions) throw new Error("useCanvasActions must be used inside NodeCanvasPage");
  return actions;
}

/**
 * 正在跟踪的任务所属的节点（起始帧联合图、单元视频、配音走流水线任务，不经画布任务回写节点状态）。
 * 单独一个 context：任务起止只重绘用到它的节点，不牵动 {@link CanvasActionsContext} 的全部消费者。
 */
export const CanvasBusyContext = createContext<ReadonlySet<string>>(new Set());

export function useBusyNodeIds(): ReadonlySet<string> {
  return useContext(CanvasBusyContext);
}
