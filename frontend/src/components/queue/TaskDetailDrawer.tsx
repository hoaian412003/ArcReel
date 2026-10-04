import { useCallback, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { ICON_BTN_FILLED_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { usePolling } from "@/hooks/usePolling";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { formatMoney, formatTimestamp, taskActions, taskCostLabels } from "@/utils/queue-format";
import { StatePanel, StatusChip } from "./queue-ui";

interface TaskDetailDrawerProps {
  taskId: string | null;
  onClose: () => void;
  /** 动作成功后通知列表刷新。 */
  onChanged: () => void;
}

type PendingAction = "cancel" | "mark_failed" | null;

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex justify-between gap-3 text-[12px]">
      <dt className="shrink-0 text-text-4">{label}</dt>
      <dd className="min-w-0 break-all text-right font-mono text-text-2">{value ?? "—"}</dd>
    </div>
  );
}

function DrawerBody({ taskId, onClose, onChanged }: Omit<TaskDetailDrawerProps, "taskId"> & { taskId: string }) {
  const { t } = useTranslation("queue");
  const titleId = useId();
  const [pending, setPending] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const { data, loading, error, refresh } = usePolling(
    (signal) => API.getQueueTask(taskId, { signal }),
    [taskId],
  );

  const closeDrawer = useCallback(() => {
    if (!pending) onClose();
  }, [pending, onClose]);
  useEscapeClose(closeDrawer);

  const run = async () => {
    setBusy(true);
    try {
      if (pending === "cancel") {
        await API.cancelQueueTask(taskId);
        useAppStore.getState().pushToast(t("action.cancel_done"), "success");
      } else {
        await API.markQueueTaskFailed(taskId, reason.trim());
        useAppStore.getState().pushToast(t("action.mark_failed_done"), "success");
      }
      setPending(null);
      setReason("");
      refresh();
      onChanged();
    } catch (err) {
      useAppStore.getState().pushToast(t("action.failed", { message: errMsg(err) }), "error");
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const task = data?.task;
  const actions = task ? taskActions(task.status) : { canCancel: false, canMarkFailed: false };
  const costs = task ? taskCostLabels(task) : null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "oklch(0 0 0 / 0.55)" }}>
      <button type="button" tabIndex={-1} aria-hidden className="absolute inset-0 cursor-default" onClick={closeDrawer} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex h-full w-full max-w-[520px] flex-col overflow-y-auto border-l border-hairline p-5"
        style={{ background: "linear-gradient(180deg, oklch(0.20 0.011 265), oklch(0.16 0.010 265))" }}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">queue · task</div>
            <h2 id={titleId} className="font-editorial mt-0.5 text-[20px] leading-tight text-text">{t("detail.title")}</h2>
            <p className="mt-0.5 truncate font-mono text-[11px] text-text-3">{taskId}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t("detail.close")} className={ICON_BTN_FILLED_CLS}>
            <X className="h-4 w-4" />
          </button>
        </div>

        {!data ? (
          <StatePanel loading={loading} error={error} empty emptyText={t("empty")} onRetry={refresh} />
        ) : (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={data.task.status} />
              {actions.canCancel && (
                <SecondaryButton size="sm" onClick={() => setPending("cancel")}>{t("action.cancel")}</SecondaryButton>
              )}
              {actions.canMarkFailed && (
                <PrimaryButton size="sm" tone="danger" onClick={() => setPending("mark_failed")}>
                  {t("action.mark_failed")}
                </PrimaryButton>
              )}
            </div>

            {data.task.error_message && (
              <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-[12px] text-red-200">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider">{t("detail.error")}</div>
                <p className="whitespace-pre-wrap break-words">{data.task.error_message}</p>
              </div>
            )}

            <section>
              <h3 className="mb-2 text-[12px] font-semibold text-text">{t("detail.summary")}</h3>
              <dl className="space-y-1.5">
                <Field label={t("col.project")} value={data.task.project_name} />
                <Field label={t("col.type")} value={`${data.task.task_type} · ${data.task.media_type}`} />
                <Field label={t("col.provider")} value={data.task.provider_id} />
                <Field label={t("detail.provider_job")} value={data.task.provider_job_id} />
                <Field label={t("detail.user")} value={data.task.user_id} />
                <Field label={t("detail.source")} value={data.task.source} />
                <Field label={t("detail.digest")} value={data.task.input_digest} />
                <Field label={t("detail.queued")} value={formatTimestamp(data.task.queued_at)} />
                <Field label={t("detail.started")} value={formatTimestamp(data.task.started_at)} />
                <Field label={t("detail.finished")} value={formatTimestamp(data.task.finished_at)} />
                <Field label={t("detail.synced")} value={formatTimestamp(data.task.last_synced_at)} />
                <Field label={t("detail.est_cost")} value={costs?.estimated ?? null} />
                <Field label={t("detail.cost")} value={costs?.actual ?? null} />
                <Field label={t("detail.coins")} value={formatMoney(data.task.provider_coins)} />
              </dl>
            </section>

            <section>
              <h3 className="mb-2 text-[12px] font-semibold text-text">{t("detail.events")}</h3>
              {data.events.length === 0 ? (
                <p className="text-[12px] text-text-4">{t("detail.events_empty")}</p>
              ) : (
                <ol className="space-y-3 border-l border-hairline pl-4">
                  {data.events.map((event) => (
                    <li key={event.id} className="relative text-[12px]">
                      <span aria-hidden className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-accent-2" />
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-mono text-[11px] font-semibold text-text">{event.kind}</span>
                        {event.stage && <span className="text-text-3">{event.stage}</span>}
                        {event.percent !== null && (
                          <span className="font-mono tabular-nums text-text-3">{Math.round(event.percent)}%</span>
                        )}
                      </div>
                      {event.message && <p className="mt-0.5 break-words text-text-2">{event.message}</p>}
                      <div className="font-mono text-[10.5px] text-text-4">{formatTimestamp(event.at)}</div>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section>
              <h3 className="mb-2 text-[12px] font-semibold text-text">{t("detail.api_calls")}</h3>
              {data.api_calls.length === 0 ? (
                <p className="text-[12px] text-text-4">{t("detail.api_calls_empty")}</p>
              ) : (
                <ul className="space-y-2">
                  {data.api_calls.map((call) => (
                    <li key={call.id} className="rounded-lg border border-hairline-soft bg-bg-grad-a/40 p-3 text-[12px]">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-mono text-text">{call.call_type} · {call.model}</span>
                        <span className="text-text-3">{call.status}</span>
                      </div>
                      <div className="mt-1 font-mono text-[11px] tabular-nums text-text-2">
                        {formatMoney(call.cost_amount, call.currency)}
                        {call.provider_coins !== null && ` · ${call.provider_coins} ${t("cost.coins")}`}
                      </div>
                      {call.error_message && <p className="mt-1 break-words text-red-300">{call.error_message}</p>}
                      <div className="mt-1 font-mono text-[10.5px] text-text-4">{formatTimestamp(call.started_at)}</div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </aside>

      <ConfirmDialog
        open={pending === "cancel"}
        title={t("action.cancel_title")}
        description={t("action.cancel_desc")}
        confirmLabel={t("action.cancel_confirm")}
        loadingLabel={t("action.cancelling")}
        tone="danger"
        loading={busy}
        onConfirm={run}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending === "mark_failed"}
        title={t("action.mark_failed_title")}
        description={
          <div className="space-y-3">
            <p>{t("action.mark_failed_desc")}</p>
            <label className="block text-[12px] text-text-3">
              {t("action.reason_label")}
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={t("action.reason_placeholder")}
                rows={3}
                className={`${INPUT_CLS} mt-1`}
              />
            </label>
          </div>
        }
        confirmLabel={t("action.mark_failed_confirm")}
        loadingLabel={t("action.marking")}
        tone="danger"
        loading={busy}
        confirmDisabled={reason.trim() === ""}
        onConfirm={run}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}

export function TaskDetailDrawer({ taskId, onClose, onChanged }: TaskDetailDrawerProps) {
  if (!taskId) return null;
  return <DrawerBody key={taskId} taskId={taskId} onClose={onClose} onChanged={onChanged} />;
}
