import { create } from "zustand";
import { ClaudeAPI, type ClaudeActionInput, type ClaudeSession } from "@/api/claude";

interface ClaudeState {
  openProject: string | null;
  selected: Record<string, string>;
  accepted: { projectName: string; session: ClaudeSession; requestId: string } | null;
  pending: Record<string, boolean>;
  open: (projectName: string) => void;
  close: () => void;
  select: (projectName: string, sessionId: string | null) => void;
  clearProject: (projectName: string) => void;
  ask: (projectName: string, action: ClaudeActionInput) => Promise<void>;
}

export const useClaudeStore = create<ClaudeState>((set, get) => ({
  openProject: null, selected: {}, accepted: null, pending: {},
  open: (projectName) => set({ openProject: projectName }),
  close: () => set({ openProject: null }),
  select: (projectName, sessionId) => set((state) => ({ selected: { ...state.selected, [projectName]: sessionId ?? "" } })),
  clearProject: (projectName) => set((state) => ({
    selected: { ...state.selected, [projectName]: "" },
    accepted: state.accepted?.projectName === projectName ? null : state.accepted,
  })),
  ask: async (projectName, action) => {
    if (get().pending[projectName]) return;
    set((state) => ({ openProject: projectName, pending: { ...state.pending, [projectName]: true } }));
    const requestId = crypto.randomUUID();
    try {
      const result = await ClaudeAPI.sendAction(projectName, action, requestId);
      set((state) => ({
        selected: { ...state.selected, [projectName]: result.session.id },
        accepted: { projectName, session: result.session, requestId },
      }));
    } finally {
      set((state) => ({ pending: { ...state.pending, [projectName]: false } }));
    }
  },
}));
