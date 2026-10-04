import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClaudeAPI, type ClaudeEvent } from "@/api/claude";
import { ClaudePanel } from "./ClaudePanel";

describe("ClaudePanel response activity", () => {
  let onStreamEvent: ((event: ClaudeEvent) => void) | undefined;

  beforeEach(() => {
    onStreamEvent = undefined;
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(ClaudeAPI, "listSessions").mockResolvedValue([]);
    vi.spyOn(ClaudeAPI, "getUsage").mockResolvedValue({
      turns: [],
      totals: {
        input_tokens: 0,
        output_tokens: 0,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
        total_cost_usd: 0,
      },
    });
    vi.spyOn(ClaudeAPI, "openEventStream").mockImplementation((options) => {
      onStreamEvent = options.onEvent;
      return {
        close: vi.fn(),
        closed: false,
        lastEventId: null,
      };
    });
  });

  it("keeps the responding signal visible after a new session is accepted", async () => {
    let acceptMessage: (() => void) | undefined;
    vi.spyOn(ClaudeAPI, "sendMessage").mockImplementation(async () => {
      await new Promise<void>((resolve) => { acceptMessage = resolve; });
      return {
        session: {
          id: "session-1",
          project_name: "demo",
          title: "Hello",
          status: "running",
          created_at: "2026-10-04T00:00:00Z",
          updated_at: "2026-10-04T00:00:00Z",
        },
        turn: {
          id: "turn-1",
          session_id: "session-1",
          client_key: "request-1",
          status: "accepted",
          input_tokens: null,
          output_tokens: null,
          cache_creation_input_tokens: null,
          cache_read_input_tokens: null,
          total_cost_usd: null,
        },
      };
    });

    render(<ClaudePanel projectName="demo" onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("给 Claude Code 发消息…"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Claude 正在回应");
    acceptMessage?.();
    await waitFor(() => expect(ClaudeAPI.openEventStream).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent("Claude 正在回应");
  });

  it("shows tool calls and updates sub-agent status in the chat timeline", async () => {
    vi.mocked(ClaudeAPI.listSessions).mockResolvedValue([{
      id: "session-1",
      project_name: "demo",
      title: "Tools",
      status: "running",
      created_at: "2026-10-04T00:00:00Z",
      updated_at: "2026-10-04T00:00:00Z",
    }]);

    render(<ClaudePanel projectName="demo" onClose={vi.fn()} />);
    await waitFor(() => expect(onStreamEvent).toBeDefined());

    onStreamEvent?.({
      session_id: "session-1",
      seq: 1,
      type: "sdk_message",
      payload: {
        message: {
          message_type: "AssistantMessage",
          content: [{ id: "agent-tool", name: "Agent", input: { description: "Inspect auth" } }],
        },
      },
      created_at: "2026-10-04T00:00:00Z",
    });
    onStreamEvent?.({
      session_id: "session-1",
      seq: 2,
      type: "sdk_message",
      payload: {
        message: {
          message_type: "TaskStartedMessage",
          task_id: "task-1",
          tool_use_id: "agent-tool",
          task_type: "explorer",
          description: "Inspect auth",
        },
      },
      created_at: "2026-10-04T00:00:01Z",
    });

    expect(await screen.findByTestId("claude-subagent")).toHaveTextContent("子代理 · explorer");
    expect(screen.getByTestId("claude-subagent")).toHaveTextContent("运行中");

    onStreamEvent?.({
      session_id: "session-1",
      seq: 3,
      type: "sdk_message",
      payload: {
        message: {
          message_type: "TaskNotificationMessage",
          task_id: "task-1",
          tool_use_id: "agent-tool",
          status: "completed",
          summary: "Auth inspected",
        },
      },
      created_at: "2026-10-04T00:00:02Z",
    });

    await waitFor(() => expect(screen.getByTestId("claude-subagent")).toHaveTextContent("已完成"));
    expect(screen.getByTestId("claude-subagent")).toHaveTextContent("Auth inspected");
  });

  it("opens as a floating non-modal window and closes with Escape", async () => {
    const onClose = vi.fn();

    render(<ClaudePanel projectName="demo" onClose={onClose} />);

    const floatingWindow = screen.getByRole("dialog", { name: "Claude Code" });
    expect(floatingWindow).toHaveClass("fixed", "bottom-3", "right-3", "claude-floating-window");
    expect(floatingWindow).toHaveAttribute("aria-modal", "false");

    fireEvent.keyDown(globalThis.window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
