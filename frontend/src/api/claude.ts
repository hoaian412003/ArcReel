import { API_BASE, parseSseJson, requestJson, sseErrorHandler, sseHeaders } from "@/api/transport";
import { openSseStream, type SseStreamError, type SseStreamHandle } from "@/utils/sse-stream";

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

export interface ClaudeLoginStart {
  login_id: string;
  authorization_url: string;
}

const base = (projectName: string) => `/projects/${encodeURIComponent(projectName)}/claude`;

export const ClaudeAPI = {
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

  resolvePermission(projectName: string, sessionId: string, requestId: string, allow: boolean) {
    return requestJson(`${base(projectName)}/sessions/${encodeURIComponent(sessionId)}/permissions/${encodeURIComponent(requestId)}`, {
      method: "POST",
      body: JSON.stringify({ allow }),
    });
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
    const url = `${API_BASE}${base(options.projectName)}/sessions/${encodeURIComponent(options.sessionId)}/events/stream`;
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
