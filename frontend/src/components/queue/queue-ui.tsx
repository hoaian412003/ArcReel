import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { statusToneClass } from "@/utils/queue-format";
import type { QueueTaskStatus } from "@/types/queue-monitor";

/** Darkroom 卡片：mono kicker 固定英文，不进 i18n。 */
export function QueueCard({
  kicker,
  title,
  children,
  className = "",
}: {
  kicker: string;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-hairline p-5 ${className}`} style={CARD_STYLE}>
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">{kicker}</div>
      {title && <h2 className="font-editorial mt-0.5 text-[18px] leading-tight text-text">{title}</h2>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function StatusChip({ status }: { status: QueueTaskStatus }) {
  const { t } = useTranslation("queue");
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusToneClass(status)}`}
    >
      {t(`status.${status}`)}
    </span>
  );
}

/** 加载 / 错误 / 空态的统一面板；有数据时调用方自行渲染内容。 */
export function StatePanel({
  loading,
  error,
  empty,
  emptyText,
  onRetry,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyText: string;
  onRetry: () => void;
}) {
  const { t } = useTranslation("queue");
  if (loading) {
    return (
      <div role="status" className="flex items-center justify-center gap-2 py-16 text-[13px] text-text-4">
        <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />
        {t("loading")}
      </div>
    );
  }
  if (error && empty) {
    return (
      <div role="alert" className="flex flex-col items-center gap-3 py-16 text-center">
        <AlertTriangle aria-hidden className="h-5 w-5 text-text-3" />
        <p className="text-[13px] text-text-2">{t("load_failed", { message: error })}</p>
        <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
          {t("retry")}
        </button>
      </div>
    );
  }
  if (empty) {
    return (
      <div className="rounded-2xl border border-dashed border-hairline bg-bg-grad-a/30 py-16 text-center text-[13px] text-text-3">
        {emptyText}
      </div>
    );
  }
  return null;
}

/** 数据已展示但最近一次轮询失败时的细条提示。 */
export function StaleBanner({ error }: { error: string | null }) {
  const { t } = useTranslation("queue");
  if (!error) return null;
  return (
    <div role="alert" className="mb-3 rounded-lg border border-hairline bg-bg-grad-a/50 px-3 py-2 text-[12px] text-text-3">
      {t("load_failed", { message: error })}
    </div>
  );
}

export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  const { t } = useTranslation("queue");
  if (total <= 0) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="mt-3 flex items-center justify-between gap-3 text-[12px] text-text-3">
      <span className="font-mono tabular-nums">{t("page_range", { from, to, total })}</span>
      <div className="flex gap-2">
        <button
          type="button"
          className={GHOST_BTN_CLS}
          disabled={offset <= 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          {t("prev_page")}
        </button>
        <button
          type="button"
          className={GHOST_BTN_CLS}
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          {t("next_page")}
        </button>
      </div>
    </div>
  );
}
