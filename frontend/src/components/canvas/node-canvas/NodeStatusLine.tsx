import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import type { CanvasNodeData } from "@/types/canvas";
import { isBusy } from "./canvas-rules";

const TICK_MS = 1000;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let now = Date.now();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** 所有状态行共用一个定时器：只有存在可见的状态行时才走表。 */
function useNow(): number {
  return useSyncExternalStore(subscribe, () => now, () => now);
}

export function formatAgo(from: number, to: number, locale: string): string {
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "narrow" });
  if (seconds < 5) return rtf.format(0, "second");
  if (seconds < 60) return rtf.format(-seconds, "second");
  if (seconds < 3600) return rtf.format(-Math.floor(seconds / 60), "minute");
  if (seconds < 86400) return rtf.format(-Math.floor(seconds / 3600), "hour");
  return rtf.format(-Math.floor(seconds / 86400), "day");
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}m ${String(seconds).padStart(2, "0")}s` : `${seconds}s`;
}

/** 节点的运行状态行：状态、提交时间、最近一次状态变化距今，运行中再显示已等待多久。 */
export function NodeStatusLine({ data }: { data: CanvasNodeData }) {
  const { t, i18n } = useTranslation("dashboard");
  const tick = useNow();
  const submitted = data.submitted_at ? Date.parse(data.submitted_at) : NaN;
  const changed = data.status_at ? Date.parse(data.status_at) : NaN;
  if (!data.status || (Number.isNaN(submitted) && Number.isNaN(changed))) return null;

  const busy = isBusy(data.status);
  const tone = data.status === "failed" ? "text-red-300" : data.status === "done" ? "text-emerald-300" : "text-text-3";
  return (
    <div className="mt-2 space-y-0.5 text-[10.5px] text-text-4" aria-live="off">
      <p className={tone}>
        {t(`node_canvas_status_${data.status}`)}
        {busy && !Number.isNaN(submitted) ? ` · ${formatElapsed(tick - submitted)}` : ""}
        {!Number.isNaN(changed) ? ` · ${t("node_canvas_updated_ago", { ago: formatAgo(changed, tick, i18n.language) })}` : ""}
      </p>
      {!Number.isNaN(submitted) ? (
        <p title={new Date(submitted).toLocaleString(i18n.language)}>
          {t("node_canvas_submitted_at", { time: new Date(submitted).toLocaleTimeString(i18n.language) })}
        </p>
      ) : null}
    </div>
  );
}
