export type QueueTaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type QueueMediaType = "image" | "video" | "audio";

export interface QueueWorkerInfo {
  online: boolean;
  owner_id: string | null;
  lease_until: string | null;
  mode: "embedded" | "external";
}

export interface QueueLane {
  provider_id: string;
  media_type: string;
  limit: number;
  running: number;
  queued: number;
}

export interface QueueOverview {
  worker: QueueWorkerInfo;
  counts: {
    queued: number;
    running: number;
    succeeded_24h: number;
    failed_24h: number;
    cancelled_24h: number;
  };
  lanes: QueueLane[];
  oldest_queued_age_seconds: number | null;
  cost_24h: {
    totals: { currency: string; amount: number }[];
    provider_coins: number;
  };
}

export interface QueueTaskRow {
  task_id: string;
  project_name: string;
  task_type: string;
  media_type: string;
  status: QueueTaskStatus;
  provider_id: string | null;
  provider_job_id: string | null;
  progress: number | null;
  last_synced_at: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  estimated_cost_amount: number | null;
  estimated_currency: string | null;
  cost_amount: number | null;
  currency: string | null;
  provider_coins: number | null;
  user_id: string;
  source: string;
}

export interface QueueTaskListFilters {
  status?: string;
  provider_id?: string;
  media_type?: string;
  project?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface QueueTaskList {
  items: QueueTaskRow[];
  total: number;
}

export interface QueueTaskEvent {
  id: number;
  at: string;
  kind: string;
  stage: string | null;
  percent: number | null;
  message: string | null;
  data: Record<string, unknown> | null;
}

export interface QueueApiCall {
  id: number;
  call_type: string;
  model: string;
  status: string;
  cost_amount: number;
  currency: string;
  provider_coins: number | null;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  inputs: Record<string, unknown> | null;
}

export interface QueueTaskDetail {
  task: QueueTaskRow & { input_digest: string | null };
  events: QueueTaskEvent[];
  api_calls: QueueApiCall[];
}

export type QueueAuditAction =
  | "enqueue_accepted"
  | "enqueue_rejected"
  | "cancel"
  | "mark_failed"
  | "limits_changed"
  | "reconciled";

export interface QueueAuditEntry {
  id: number;
  at: string;
  actor: string;
  action: QueueAuditAction;
  task_id: string | null;
  project_name: string | null;
  task_type: string | null;
  detail: Record<string, unknown>;
}

export interface QueueAuditFilters {
  action?: string;
  user_id?: string;
  project?: string;
  limit?: number;
  offset?: number;
}

export interface QueueAuditList {
  items: QueueAuditEntry[];
  total: number;
}

export type QueueLimitOverrides = Partial<Record<QueueMediaType, number | null>>;

export interface QueueLimits {
  defaults: Record<QueueMediaType, number>;
  overrides: Record<QueueMediaType, number | null>;
  effective: { provider_id: string; media_type: string; limit: number }[];
}
