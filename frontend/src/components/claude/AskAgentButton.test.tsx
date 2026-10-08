import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClaudeAPI } from "@/api/claude";
import { useClaudeStore } from "@/stores/claude-store";
import { useAppStore } from "@/stores/app-store";
import { AskAgentButton } from "./AskAgentButton";

const result = {
  session: { id: "main", project_name: "demo", title: "Project", status: "idle", created_at: "", updated_at: "" },
  turn: { id: "turn", session_id: "main", client_key: "action", status: "accepted", input_tokens: null, output_tokens: null, cache_creation_input_tokens: null, cache_read_input_tokens: null, total_cost_usd: null },
};

beforeEach(() => { useClaudeStore.setState({ openProject: null, selected: {}, accepted: null, pending: {} }); });

describe("AskAgentButton", () => {
  it("sends EP04 immediately and opens the same project's main conversation", async () => {
    vi.spyOn(ClaudeAPI, "sendAction").mockResolvedValue(result);
    render(<AskAgentButton projectName="demo" action={{ action: "create_screenplay", episode: 4 }} />);
    fireEvent.click(screen.getByRole("button", { name: "请 Agent 处理" }));
    await waitFor(() => expect(ClaudeAPI.sendAction).toHaveBeenCalledWith("demo", { action: "create_screenplay", episode: 4 }, expect.any(String)));
    await waitFor(() => expect(useClaudeStore.getState().selected.demo).toBe("main"));
    expect(useClaudeStore.getState().openProject).toBe("demo");
  });

  it("prevents another submission while accepting an action", async () => {
    let finish: ((value: typeof result) => void) | undefined;
    vi.spyOn(ClaudeAPI, "sendAction").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<AskAgentButton projectName="demo" action={{ action: "create_screenplay", episode: 4 }} />);
    const button = screen.getByRole("button", { name: "请 Agent 处理" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(ClaudeAPI.sendAction).toHaveBeenCalledTimes(1);
    finish?.(result);
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("shows a failed request and allows retry", async () => {
    vi.spyOn(ClaudeAPI, "sendAction").mockRejectedValue(new Error("Connect Claude"));
    render(<AskAgentButton projectName="demo" action={{ action: "create_screenplay", episode: 4 }} />);
    fireEvent.click(screen.getByRole("button", { name: "请 Agent 处理" }));
    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("Connect Claude"));
    expect(screen.getByRole("button", { name: "请 Agent 处理" })).toBeEnabled();
  });
});
