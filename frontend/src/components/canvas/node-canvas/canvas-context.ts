import { createContext, useContext } from "react";

export interface CanvasActions {
  projectName: string;
  setText: (nodeId: string, text: string) => Promise<void>;
  setAspectRatio: (nodeId: string, aspectRatio: string) => Promise<void>;
  run: (nodeId: string) => Promise<void>;
  remove: (nodeId: string) => Promise<void>;
  /** H3 写作节点：为它所连的视频单元重新生成 H3 提示词。 */
  runWriter: (nodeId: string) => Promise<void>;
  /** 起始帧联合图节点：生成这张联合图。 */
  runKeyframe: (episode: number, sheetId: string) => Promise<void>;
  /** 正在跟踪的任务所属的节点（写作节点 / 联合图节点）。 */
  busyNodeIds: ReadonlySet<string>;
}

export const CanvasActionsContext = createContext<CanvasActions | null>(null);

export function useCanvasActions(): CanvasActions {
  const actions = useContext(CanvasActionsContext);
  if (!actions) throw new Error("useCanvasActions must be used inside NodeCanvasPage");
  return actions;
}
