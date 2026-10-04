import { useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { usePolling } from "@/hooks/usePolling";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { parseLimitInput } from "@/utils/queue-format";
import type { QueueLimits, QueueMediaType } from "@/types/queue-monitor";
import { QueueCard, StatePanel } from "./queue-ui";

const MEDIA: readonly QueueMediaType[] = ["image", "video", "audio"];

type Draft = Record<QueueMediaType, string>;

function toDraft(limits: QueueLimits): Draft {
  const pick = (m: QueueMediaType) => (limits.overrides[m] === null ? "" : String(limits.overrides[m]));
  return { image: pick("image"), video: pick("video"), audio: pick("audio") };
}

function LimitsForm({ limits, onSaved }: { limits: QueueLimits; onSaved: (next: QueueLimits) => void }) {
  const { t } = useTranslation("queue");
  const [draft, setDraft] = useState<Draft>(() => toDraft(limits));
  const [saving, setSaving] = useState(false);

  const parsed = MEDIA.map((m) => [m, parseLimitInput(draft[m])] as const);
  const hasInvalid = parsed.some(([, v]) => v === undefined);
  const dirty = MEDIA.some((m) => draft[m] !== toDraft(limits)[m]);

  const save = async () => {
    setSaving(true);
    try {
      const overrides = Object.fromEntries(parsed) as Record<QueueMediaType, number | null>;
      const next = await API.updateQueueLimits(overrides);
      setDraft(toDraft(next));
      onSaved(next);
      useAppStore.getState().pushToast(t("limits.saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("limits.save_failed", { message: errMsg(err) }), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <QueueCard kicker="queue · limits" title={t("limits.title")}>
        <p className="mb-4 text-[12.5px] text-text-3">{t("limits.desc")}</p>
        <div className="grid gap-4 sm:grid-cols-3">
          {MEDIA.map((m) => {
            const invalid = parseLimitInput(draft[m]) === undefined;
            return (
              <div key={m}>
                <label htmlFor={`limit-${m}`} className="mb-1 block text-[12px] font-medium text-text-2">
                  {t(`limits.field.${m}`)}
                </label>
                <input
                  id={`limit-${m}`}
                  inputMode="numeric"
                  value={draft[m]}
                  placeholder={String(limits.defaults[m])}
                  aria-invalid={invalid}
                  aria-describedby={`limit-${m}-hint`}
                  onChange={(e) => setDraft((prev) => ({ ...prev, [m]: e.target.value }))}
                  className={INPUT_CLS}
                />
                <p id={`limit-${m}-hint`} className={`mt-1 text-[11px] ${invalid ? "text-red-300" : "text-text-4"}`}>
                  {invalid ? t("limits.invalid") : t("limits.default", { value: limits.defaults[m] })}
                </p>
              </div>
            );
          })}
        </div>
        <div className="mt-5">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || hasInvalid || !dirty}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {saving ? t("limits.saving") : t("limits.save")}
          </button>
        </div>
      </QueueCard>

      <QueueCard kicker="queue · effective" title={t("limits.effective")}>
        {limits.effective.length === 0 ? (
          <p className="text-[12px] text-text-4">{t("limits.effective_empty")}</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {limits.effective.map((lane) => (
              <li
                key={`${lane.provider_id}/${lane.media_type}`}
                className="flex items-baseline justify-between gap-2 rounded-lg border border-hairline-soft bg-bg-grad-a/40 px-3 py-2 text-[12.5px]"
              >
                <span className="truncate text-text">
                  {lane.provider_id}
                  <span className="ml-1.5 font-mono text-[10px] uppercase tracking-wider text-text-4">{lane.media_type}</span>
                </span>
                <span className="font-mono tabular-nums text-text-2">{lane.limit}</span>
              </li>
            ))}
          </ul>
        )}
      </QueueCard>
    </div>
  );
}

export function QueueLimitsTab() {
  const { t } = useTranslation("queue");
  const [saved, setSaved] = useState<QueueLimits | null>(null);
  // 限制值很少变化，且表单持有未保存草稿：只在挂载时取一次（保存响应直接作为新基线），不做轮询覆盖。
  const { data, loading, error, refresh } = usePolling((signal) => API.getQueueLimits({ signal }), [], 60 * 60 * 1000);
  const limits = saved ?? data;

  if (!limits) {
    return <StatePanel loading={loading} error={error} empty emptyText={t("empty")} onRetry={refresh} />;
  }
  return (
    <LimitsForm
      key={JSON.stringify(limits.overrides)}
      limits={limits}
      onSaved={setSaved}
    />
  );
}
