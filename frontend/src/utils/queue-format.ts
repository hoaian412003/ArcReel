import type { QueueTaskRow, QueueTaskStatus } from "@/types/queue-monitor";

/** 秒数 → 紧凑时长读数（45s / 3m 05s / 2h 10m / 1d 4h）；null 或负数返回 null。 */
export function formatAgeSeconds(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.floor(seconds);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (total < 3600) return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
  const hours = Math.floor(total / 3600);
  if (total < 86400) return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
  return `${Math.floor(total / 86400)}d ${hours % 24}h`;
}

/** 金额读数：保留至多 4 位小数并去掉多余尾零；币种缺省时只给数字。 */
export function formatMoney(amount: number | null | undefined, currency?: string | null): string | null {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return null;
  const text = Number(amount.toFixed(4)).toString();
  return currency ? `${text} ${currency}` : text;
}

/** 并发车道占用率（0-100，上限为 100）；limit 非正时视为 0。 */
export function laneUsagePercent(running: number, limit: number): number {
  if (!(limit > 0)) return 0;
  return Math.min(100, Math.max(0, Math.round((running / limit) * 100)));
}

export function isTerminalStatus(status: QueueTaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/** 任务动作的可用性：后端只允许取消排队中、标记运行中。 */
export function taskActions(status: QueueTaskStatus): { canCancel: boolean; canMarkFailed: boolean } {
  return { canCancel: status === "queued", canMarkFailed: status === "running" };
}

/** 估算与实际成本的并排读数；两者都缺失时各自为 null。 */
export function taskCostLabels(task: Pick<
  QueueTaskRow,
  "estimated_cost_amount" | "estimated_currency" | "cost_amount" | "currency"
>): { estimated: string | null; actual: string | null } {
  return {
    estimated: formatMoney(task.estimated_cost_amount, task.estimated_currency),
    actual: formatMoney(task.cost_amount, task.currency),
  };
}

const STATUS_TONE: Record<QueueTaskStatus, string> = {
  queued: "border-hairline text-text-3 bg-bg-grad-a/50",
  running: "border-accent/45 text-accent-2 bg-accent-dim",
  succeeded: "border-emerald-500/35 text-emerald-300 bg-emerald-500/10",
  failed: "border-red-500/40 text-red-300 bg-red-500/10",
  cancelled: "border-hairline text-text-4 bg-bg-grad-b/60",
};

export function statusToneClass(status: QueueTaskStatus): string {
  return STATUS_TONE[status];
}

/** 把表单里的并发数文本解析成合法值：空串 → null（沿用默认），正整数 → 数字，其余 → undefined（非法）。 */
export function parseLimitInput(raw: string): number | null | undefined {
  const text = raw.trim();
  if (text === "") return null;
  if (!/^\d+$/.test(text)) return undefined;
  const value = Number(text);
  return value >= 1 && value <= 1000 ? value : undefined;
}

/** ISO 时间 → 本地化日期时间；缺失或无法解析时返回 "—"。 */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}
