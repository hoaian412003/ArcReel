import { create } from "zustand";

/** 画布页向 Agent 面板提供的能力：列出可引用的节点，并把用户的话连同被引用节点的内容拼成发给 Agent 的消息。 */
export interface CanvasContextProvider {
  nodeIds: () => string[];
  compose: (text: string, contextIds: readonly string[]) => string;
}

interface CanvasContextState {
  ids: Record<string, string[]>;
  providers: Record<string, CanvasContextProvider>;
  add: (projectName: string, nodeId: string) => void;
  remove: (projectName: string, nodeId: string) => void;
  clear: (projectName: string) => void;
  register: (projectName: string, provider: CanvasContextProvider | null) => void;
}

export const useCanvasContextStore = create<CanvasContextState>((set) => ({
  ids: {},
  providers: {},
  add: (projectName, nodeId) =>
    set((s) => {
      const current = s.ids[projectName] ?? [];
      return current.includes(nodeId) ? s : { ids: { ...s.ids, [projectName]: [...current, nodeId] } };
    }),
  remove: (projectName, nodeId) =>
    set((s) => ({ ids: { ...s.ids, [projectName]: (s.ids[projectName] ?? []).filter((id) => id !== nodeId) } })),
  clear: (projectName) => set((s) => ({ ids: { ...s.ids, [projectName]: [] } })),
  register: (projectName, provider) =>
    set((s) => {
      const providers = { ...s.providers };
      if (provider) providers[projectName] = provider;
      else delete providers[projectName];
      return { providers };
    }),
}));
