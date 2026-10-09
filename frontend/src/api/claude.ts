import { API_BASE, parseSseJson, requestJson, sseErrorHandler, sseHeaders, withAuth, throwIfNotOk } from "@/api/transport";
import { openSseStream, type SseStreamError, type SseStreamHandle } from "@/utils/sse-stream";

/** 首次连接只回放最近 N 轮，更早的历史按需通过 getEarlierEvents 分页获取。 */
export const INITIAL_TAIL_TURNS = 10;

export interface ClaudeSession {
  id: string;
  project_name: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ClaudeTurn {
  id: string;
  session_id: string;
  client_key: string;
  status: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_input_tokens: number | null;
  cache_read_input_tokens: number | null;
  total_cost_usd: number | null;
}

export interface ClaudeEvent {
  session_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ClaudeUsageTotals {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_cost_usd: number;
}

export interface ClaudeAuthStatus {
  logged_in: boolean;
  login_pending: boolean;
  auth_method: string | null;
  api_provider: string | null;
  email: string | null;
  organization: string | null;
  subscription_type: string | null;
}

export interface ClaudeActionInput {
  action: "create_screenplay" | "create_direction" | "continue_episode" | "redo_episode" | "author_shot" | "author_video_unit" | "repair_project" | "remember" | "canvas_assist";
  episode?: number;
  resource_id?: string;
  script_file?: string;
  note?: string;
}

export interface ProjectClaudeAgent {
  agent_id: string;
  main_session_id: string | null;
  workspace: string;
  memory_path: string;
  memory_enabled: boolean;
  logged_in: boolean;
  mcp_available: boolean;
  skills: string[];
  sessions: ClaudeSession[];
}

export interface ClaudeMemoryFile { name: string; content: string }

export interface ClaudeLoginStart {
  login_id: string;
  authorization_url: string;
}

const base = (projectName: string) => `/projects/${encodeURIComponent(projectName)}/claude`;

export const ClaudeAPI = {
  getAgent(projectName: string) {
    return requestJson<ProjectClaudeAgent>(`${base(projectName)}/agent`);
  },
  sendAction(projectName: string, action: ClaudeActionInput, clientKey: string) {
    return requestJson<{ session: ClaudeSession; turn: ClaudeTurn }>(`${base(projectName)}/actions`, {
      method: "POST", body: JSON.stringify({ ...action, client_key: clientKey }),
    });
  },
  getMemory(projectName: string) {
    return requestJson<{ files: ClaudeMemoryFile[] }>(`${base(projectName)}/memory`);
  },
  saveMemory(projectName: string, file: ClaudeMemoryFile) {
    return requestJson(`${base(projectName)}/memory`, { method: "PUT", body: JSON.stringify(file) });
  },
  promoteMemory(projectName: string, name: string) {
    return requestJson(`${base(projectName)}/memory/promote`, { method: "POST", body: JSON.stringify({ name }) });
  },
  resetMemory(projectName: string) {
    return requestJson(`${base(projectName)}/memory/reset`, { method: "POST" });
  },
  async exportAgent(projectName: string) {
    const endpoint = `${base(projectName)}/export`;
    const response = await fetch(`${API_BASE}${endpoint}`, withAuth(endpoint));
    await throwIfNotOk(response, response.statusText);
    return response.blob();
  },
  authStatus() {
    return requestJson<ClaudeAuthStatus>("/claude/auth/status");
  },

  startLogin() {
    return requestJson<ClaudeLoginStart>("/claude/auth/login", { method: "POST" });
  },

  completeLogin(loginId: string, code: string) {
    return requestJson<ClaudeAuthStatus>(`/claude/auth/login/${encodeURIComponent(loginId)}/complete`, {
      method: "POST",
      body: JSON.stringify({ code }),
    });
  },

  logout() {
    return requestJson<ClaudeAuthStatus>("/claude/auth/logout", { method: "POST" });
  },

  async listSessions(projectName: string): Promise<ClaudeSession[]> {
    const data = await requestJson<{ sessions: ClaudeSession[] }>(`${base(projectName)}/sessions`);
    return data.sessions;
  },

  clearSessions(projectName: string) {
    return requestJson<{ deleted: number }>(`${base(projectName)}/sessions`, { method: "DELETE" });
  },

  sendMessage(projectName: string, content: string, sessionId: string | null, clientKey: string) {
    return requestJson<{ session: ClaudeSession; turn: ClaudeTurn }>(`${base(projectName)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, session_id: sessionId, client_key: clientKey }),
    });
  },

  async getUsage(projectName: string, sessionId: string) {
    return requestJson<{ turns: ClaudeTurn[]; totals: ClaudeUsageTotals }>(
      `${base(projectName)}/sessions/${encodeURIComponent(sessionId)}/turns`,
    );
  },

  getEarlierEvents(projectName: string, sessionId: string, beforeSeq: number, turns: number) {
    return requestJson<{ events: ClaudeEvent[]; has_more: boolean }>(
      `${base(projectName)}/sessions/${encodeURIComponent(sessionId)}/events/earlier?before_seq=${beforeSeq}&turns=${turns}`,
    );
  },

  resolvePermission(projectName: string, sessionId: string, requestId: string, allow: boolean) {
    return requestJson(`${base(projectName)}/sessions/${encodeURIComponent(sessionId)}/permissions/${encodeURIComponent(requestId)}`, {
      method: "POST",
      body: JSON.stringify({ allow }),
    });
  },

  restoreSnapshot(projectName: string, snapshotId: string) {
    return requestJson<{ restored: { added: string[]; modified: string[]; removed: string[] } }>(
      `${base(projectName)}/snapshots/${encodeURIComponent(snapshotId)}/restore`,
      { method: "POST" },
    );
  },

  interrupt(projectName: string, sessionId: string) {
    return requestJson(`${base(projectName)}/sessions/${encodeURIComponent(sessionId)}/interrupt`, { method: "POST" });
  },

  openEventStream(options: {
    projectName: string;
    sessionId: string;
    onEvent: (event: ClaudeEvent) => void;
    onError?: (error: SseStreamError) => void;
  }): SseStreamHandle {
    const url = `${API_BASE}${base(options.projectName)}/sessions/${encodeURIComponent(options.sessionId)}/events/stream?tail_turns=${INITIAL_TAIL_TURNS}`;
    return openSseStream({
      url,
      headers: sseHeaders,
      onMessage(message) {
        const parsed = parseSseJson(message.data, "Claude");
        if (parsed) options.onEvent(parsed as unknown as ClaudeEvent);
      },
      onError: sseErrorHandler(options.onError),
    });
  },
};
