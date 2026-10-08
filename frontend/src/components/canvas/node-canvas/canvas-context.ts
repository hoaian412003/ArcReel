import { createContext, useContext } from "react";
import type { CharacterAssets } from "@/components/canvas/lorebook/useCharacterAssets";
import type { Character } from "@/types";

export interface ImageModelChoices {
  /** `供应商/模型` 候选。 */
  options: string[];
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
}

export interface CanvasActions {
  projectName: string;
  setText: (nodeId: string, text: string) => Promise<void>;
  setAspectRatio: (nodeId: string, aspectRatio: string) => Promise<void>;
  /** 通用字段补丁（时长、音色等供应商节点参数）。 */
  setData: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
  /** 空串恢复项目默认。 */
  setModel: (nodeId: string, model: string) => Promise<void>;
  imageModels: ImageModelChoices;
  run: (nodeId: string) => Promise<void>;
  remove: (nodeId: string) => Promise<void>;
  /** H3 写作节点：为它所连的视频单元重新生成 H3 提示词。 */
  runWriter: (nodeId: string) => Promise<void>;
  /** 起始帧联合图节点：生成这张联合图。 */
  runKeyframe: (episode: number, sheetId: string) => Promise<void>;
}

export const CanvasActionsContext = createContext<CanvasActions | null>(null);

export function useCanvasActions(): CanvasActions {
  const actions = useContext(CanvasActionsContext);
  if (!actions) throw new Error("useCanvasActions must be used inside NodeCanvasPage");
  return actions;
}

/**
 * 正在跟踪的任务所属的节点（H3 写作节点 id / `集:联合图`）。单独一个 context：任务起止只重绘用到它的
 * 两类节点，不牵动 {@link CanvasActionsContext} 的全部消费者。
 */
export const CanvasBusyContext = createContext<ReadonlySet<string>>(new Set());

export function useBusyNodeIds(): ReadonlySet<string> {
  return useContext(CanvasBusyContext);
}

/** 角色分组里的节点（描述文本、参考图、图片供应商、资产图）所需的项目数据与写操作；由画布页提供，与演示项目的角色页共用 {@link CharacterAssets}。 */
export interface CharacterNodeContext {
  characters: Record<string, Character>;
  assets: CharacterAssets;
}

export const CharacterNodeContextValue = createContext<CharacterNodeContext | null>(null);

export function useCharacterNodeContext(): CharacterNodeContext | null {
  return useContext(CharacterNodeContextValue);
}
