import { describe, expect, it } from "vitest";
import type { ClaudeEvent } from "@/api/claude";
import { projectClaudeTimeline } from "./claudeTimeline";

function event(seq: number, message: Record<string, unknown>): ClaudeEvent {
  return {
    session_id: "session-1",
    seq,
    type: "sdk_message",
    payload: { message },
    created_at: "2026-10-04T00:00:00Z",
  };
}

describe("projectClaudeTimeline", () => {
  it("merges a tool call and its result into one completed activity", () => {
    const timeline = projectClaudeTimeline([
      event(1, {
        message_type: "AssistantMessage",
        content: [{ id: "tool-1", name: "Read", input: { file_path: "README.md" } }],
      }),
      event(2, {
        message_type: "UserMessage",
        content: [{ tool_use_id: "tool-1", content: "hello", is_error: false }],
      }),
    ], new Set());

    expect(timeline).toEqual([
      expect.objectContaining({
        kind: "activity",
        activityKind: "tool",
        name: "Read",
        status: "completed",
        result: "hello",
      }),
    ]);
  });

  it("merges sub-agent progress and preserves nested tool parentage", () => {
    const timeline = projectClaudeTimeline([
      event(1, {
        message_type: "AssistantMessage",
        content: [{ id: "agent-tool", name: "Agent", input: { description: "Inspect auth" } }],
      }),
      event(2, {
        message_type: "TaskStartedMessage",
        task_id: "task-1",
        tool_use_id: "agent-tool",
        task_type: "explorer",
        description: "Inspect auth",
      }),
      event(3, {
        message_type: "AssistantMessage",
        parent_tool_use_id: "agent-tool",
        content: [{ id: "child-tool", name: "Grep", input: { pattern: "auth" } }],
      }),
      event(4, {
        message_type: "TaskProgressMessage",
        task_id: "task-1",
        tool_use_id: "agent-tool",
        description: "Checking routes",
        last_tool_name: "Grep",
        usage: { total_tokens: 120 },
      }),
      event(5, {
        message_type: "TaskNotificationMessage",
        task_id: "task-1",
        tool_use_id: "agent-tool",
        status: "completed",
        summary: "Auth inspected",
      }),
    ], new Set());

    expect(timeline).toHaveLength(2);
    expect(timeline[0]).toEqual(expect.objectContaining({
      kind: "activity",
      activityKind: "subagent",
      name: "explorer",
      taskId: "task-1",
      status: "completed",
      description: "Auth inspected",
      lastToolName: "Grep",
    }));
    expect(timeline[1]).toEqual(expect.objectContaining({
      kind: "activity",
      activityKind: "tool",
      parentToolUseId: "agent-tool",
      status: "running",
    }));
  });

  it("marks failed tool results as errors", () => {
    const timeline = projectClaudeTimeline([
      event(1, {
        message_type: "AssistantMessage",
        content: [{ id: "tool-1", name: "Bash", input: { command: "false" } }],
      }),
      event(2, {
        message_type: "UserMessage",
        content: [{ tool_use_id: "tool-1", content: "exit 1", is_error: true }],
      }),
    ], new Set());

    expect(timeline[0]).toEqual(expect.objectContaining({ status: "error" }));
  });
});

describe("project changes", () => {
  it("projects a project_changes event into a revertable summary", () => {
    const timeline = projectClaudeTimeline(
      [
        {
          session_id: "session-1",
          seq: 4,
          type: "project_changes",
          payload: { snapshot_id: "snap-1", modified: ["project.json"], added: [], removed: [], total: 1, valid: false, errors: 2 },
          created_at: "2026-10-04T00:00:00Z",
        },
      ],
      new Set(),
    );

    expect(timeline).toEqual([
      expect.objectContaining({
        kind: "changes",
        changes: expect.objectContaining({ snapshotId: "snap-1", modified: ["project.json"], valid: false, errors: 2 }),
      }),
    ]);
  });
});
