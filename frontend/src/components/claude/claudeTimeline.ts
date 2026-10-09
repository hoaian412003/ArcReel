import type { ClaudeEvent } from "@/api/claude";

type ActivityStatus = "running" | "completed" | "error" | "stopped";

/** 一轮 Agent 对话改动项目文本文件后的摘要；`snapshotId` 对应开始前的快照，可整体回退。 */
export interface ProjectChangeSummary {
  snapshotId: string;
  added: string[];
  modified: string[];
  removed: string[];
  total: number;
  valid: boolean;
  errors: number;
  warnings: number;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

export type ClaudeTimelineItem =
  | { kind: "user"; key: string; seq: number; text: string }
  | { kind: "assistant"; key: string; seq: number; text: string }
  | { kind: "permission"; key: string; seq: number; event: ClaudeEvent }
  | { kind: "changes"; key: string; seq: number; changes: ProjectChangeSummary }
  | {
    kind: "activity";
    key: string;
    seq: number;
    activityKind: "tool" | "subagent";
    name: string;
    status: ActivityStatus;
    input?: unknown;
    result?: unknown;
    description?: string;
    lastToolName?: string;
    parentToolUseId?: string;
    toolUseId?: string;
    taskId?: string;
    usage?: Record<string, unknown>;
  };

export type ClaudeActivityItem = Extract<ClaudeTimelineItem, { kind: "activity" }>;

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function messageRecord(event: ClaudeEvent): Record<string, unknown> | null {
  return record(event.payload.message);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function assistantText(message: Record<string, unknown>): string | null {
  if (message.message_type !== "AssistantMessage" || !Array.isArray(message.content)) return null;
  const text = message.content
    .map((block) => stringValue(record(block)?.text) ?? "")
    .join("");
  return text || null;
}

function isSubagentTool(name: string): boolean {
  return /^(agent|task)$/i.test(name);
}

function resultBlocks(message: Record<string, unknown>): Record<string, unknown>[] {
  if (!Array.isArray(message.content)) return [];
  return message.content
    .map(record)
    .filter((block): block is Record<string, unknown> => Boolean(block && stringValue(block.tool_use_id)));
}

function toolUseBlocks(message: Record<string, unknown>): Record<string, unknown>[] {
  if (!Array.isArray(message.content)) return [];
  return message.content
    .map(record)
    .filter((block): block is Record<string, unknown> => Boolean(
      block && stringValue(block.id) && stringValue(block.name) && "input" in block,
    ));
}

function taskStatus(value: unknown): ActivityStatus {
  if (value === "completed") return "completed";
  if (value === "failed") return "error";
  if (value === "stopped" || value === "killed" || value === "paused") return "stopped";
  return "running";
}

export function projectClaudeTimeline(
  events: ClaudeEvent[],
  resolvedPermissions: ReadonlySet<string>,
): ClaudeTimelineItem[] {
  const items: ClaudeTimelineItem[] = [];
  const toolIndexes = new Map<string, number>();
  const taskIndexes = new Map<string, number>();

  const updateActivity = (index: number, patch: Partial<ClaudeActivityItem>) => {
    const current = items[index];
    if (current?.kind !== "activity") return;
    const definedPatch = Object.fromEntries(
      Object.entries(patch).filter(([, value]) => value !== undefined),
    ) as Partial<ClaudeActivityItem>;
    items[index] = { ...current, ...definedPatch };
  };

  for (const event of events) {
    if (event.type === "user_message" && typeof event.payload.content === "string") {
      items.push({ kind: "user", key: `user-${event.seq}`, seq: event.seq, text: event.payload.content });
      continue;
    }
    if (event.type === "permission_request") {
      const requestId = stringValue(event.payload.request_id);
      if (!requestId || !resolvedPermissions.has(requestId)) {
        items.push({ kind: "permission", key: `permission-${event.seq}`, seq: event.seq, event });
      }
      continue;
    }
    if (event.type === "project_changes") {
      const snapshotId = stringValue(event.payload.snapshot_id);
      if (snapshotId) {
        items.push({
          kind: "changes",
          key: `changes-${event.seq}`,
          seq: event.seq,
          changes: {
            snapshotId,
            added: stringList(event.payload.added),
            modified: stringList(event.payload.modified),
            removed: stringList(event.payload.removed),
            total: numberValue(event.payload.total),
            valid: event.payload.valid !== false,
            errors: numberValue(event.payload.errors),
            warnings: numberValue(event.payload.warnings),
          },
        });
      }
      continue;
    }
    if (event.type !== "sdk_message") continue;

    const message = messageRecord(event);
    if (!message) continue;
    const type = stringValue(message.message_type);
    const text = assistantText(message);
    if (text) items.push({ kind: "assistant", key: `assistant-${event.seq}`, seq: event.seq, text });

    for (const block of toolUseBlocks(message)) {
      const id = stringValue(block.id);
      const name = stringValue(block.name);
      if (!id || !name || toolIndexes.has(id)) continue;
      const item: ClaudeActivityItem = {
        kind: "activity",
        key: `tool-${id}`,
        seq: event.seq,
        activityKind: isSubagentTool(name) ? "subagent" : "tool",
        name,
        status: "running",
        input: block.input,
        parentToolUseId: stringValue(message.parent_tool_use_id),
        toolUseId: id,
      };
      toolIndexes.set(id, items.length);
      items.push(item);
    }

    for (const block of resultBlocks(message)) {
      const id = stringValue(block.tool_use_id);
      if (!id) continue;
      const index = toolIndexes.get(id);
      if (index !== undefined) {
        updateActivity(index, {
          status: block.is_error === true ? "error" : "completed",
          result: block.content,
        });
      }
    }

    if (type === "TaskStartedMessage") {
      const taskId = stringValue(message.task_id);
      const toolUseId = stringValue(message.tool_use_id);
      if (!taskId) continue;
      const existingIndex = toolUseId ? toolIndexes.get(toolUseId) : undefined;
      if (existingIndex !== undefined) {
        taskIndexes.set(taskId, existingIndex);
        updateActivity(existingIndex, {
          activityKind: "subagent",
          taskId,
          status: "running",
          description: stringValue(message.description),
          name: stringValue(message.task_type) ?? "Agent",
        });
      } else {
        const item: ClaudeActivityItem = {
          kind: "activity",
          key: `task-${taskId}`,
          seq: event.seq,
          activityKind: "subagent",
          name: stringValue(message.task_type) ?? "Agent",
          status: "running",
          description: stringValue(message.description),
          taskId,
          toolUseId,
        };
        taskIndexes.set(taskId, items.length);
        if (toolUseId) toolIndexes.set(toolUseId, items.length);
        items.push(item);
      }
      continue;
    }

    if (type === "TaskProgressMessage" || type === "TaskNotificationMessage" || type === "TaskUpdatedMessage") {
      const taskId = stringValue(message.task_id);
      const toolUseId = stringValue(message.tool_use_id);
      const index = (taskId ? taskIndexes.get(taskId) : undefined)
        ?? (toolUseId ? toolIndexes.get(toolUseId) : undefined);
      if (index === undefined) continue;
      const patch: Partial<ClaudeActivityItem> = {
        status: type === "TaskProgressMessage" ? "running" : taskStatus(message.status),
        description: stringValue(message.summary) ?? stringValue(message.description),
        lastToolName: stringValue(message.last_tool_name),
      };
      const usage = record(message.usage);
      if (usage) patch.usage = usage;
      updateActivity(index, patch);
    }
  }

  return items;
}

export function activitySummary(activity: ClaudeActivityItem): string | null {
  if (activity.description) return activity.description;
  const input = record(activity.input);
  if (!input) return typeof activity.input === "string" ? activity.input : null;
  for (const key of ["description", "file_path", "path", "command", "query", "pattern", "url", "prompt"]) {
    const value = stringValue(input[key]);
    if (value) return value;
  }
  return null;
}

export function formatActivityDetail(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return null;
  }
}
