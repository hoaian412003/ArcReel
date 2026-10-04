import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { API } from "@/api";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { usePolling } from "@/hooks/usePolling";
import { formatMoney, formatTimestamp, taskCostLabels } from "@/utils/queue-format";
import type { QueueTaskRow } from "@/types/queue-monitor";
import { Pager, StaleBanner, StatePanel, StatusChip } from "./queue-ui";
import { TaskDetailDrawer } from "./TaskDetailDrawer";

const PAGE_SIZE = 25;
const STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;
const MEDIA_TYPES = ["image", "video", "audio"] as const;

function CostCell({ task }: { task: QueueTaskRow }) {
  const { estimated, actual } = taskCostLabels(task);
  return (
    <div className="font-mono text-[11.5px] tabular-nums">
      <div className="text-text-2" title={actual ?? undefined}>{actual ?? "—"}</div>
      {estimated && <div className="text-text-4">~{estimated}</div>}
    </div>
  );
}

export function QueueTasksTab() {
  const { t } = useTranslation("queue");
  const [status, setStatus] = useState("");
  const [media, setMedia] = useState("");
  const [provider, setProvider] = useState("");
  const [project, setProject] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  const debounced = useDebouncedValue({ provider, project, q }, 300);

  useEffect(() => {
    // 过滤条件变化后回到第一页
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOffset(0);
  }, [status, media, debounced]);

  const { data, loading, error, refresh } = usePolling(
    (signal) =>
      API.listQueueTasks(
        {
          status,
          media_type: media,
          provider_id: debounced.provider,
          project: debounced.project,
          q: debounced.q,
          limit: PAGE_SIZE,
          offset,
        },
        { signal },
      ),
    [status, media, debounced, offset],
  );

  const items = data?.items ?? [];

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-4" />
          <input
            type="search"
            aria-label={t("filter.search")}
            placeholder={t("filter.search")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className={`${INPUT_CLS} pl-8`}
          />
        </div>
        <select aria-label={t("filter.status")} value={status} onChange={(e) => setStatus(e.target.value)} className={`${INPUT_CLS} !w-auto`}>
          <option value="">{t("status.all")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{t(`status.${s}`)}</option>
          ))}
        </select>
        <select aria-label={t("filter.media")} value={media} onChange={(e) => setMedia(e.target.value)} className={`${INPUT_CLS} !w-auto`}>
          <option value="">{t("media.all")}</option>
          {MEDIA_TYPES.map((m) => (
            <option key={m} value={m}>{t(`media.${m}`)}</option>
          ))}
        </select>
        <input
          aria-label={t("filter.provider")}
          placeholder={t("filter.provider")}
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className={`${INPUT_CLS} !w-[140px]`}
        />
        <input
          aria-label={t("filter.project")}
          placeholder={t("filter.project")}
          value={project}
          onChange={(e) => setProject(e.target.value)}
          className={`${INPUT_CLS} !w-[140px]`}
        />
      </div>

      <StaleBanner error={items.length > 0 ? error : null} />
      <StatePanel loading={loading && !data} error={error} empty={items.length === 0} emptyText={t("tasks.empty")} onRetry={refresh} />

      {items.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-hairline">
          <table className="w-full min-w-[860px] text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-hairline text-[11px] uppercase tracking-wider text-text-4">
                {["col.task", "col.project", "col.status", "col.provider", "col.progress", "col.cost", "col.coins", "col.queued_at"].map((key) => (
                  <th key={key} scope="col" className="px-3 py-2.5 font-medium">{t(key)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((task) => (
                <tr key={task.task_id} className="border-b border-hairline-soft last:border-0 hover:bg-bg-grad-a/40">
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      onClick={() => setSelected(task.task_id)}
                      aria-label={t("tasks.open_detail", { id: task.task_id })}
                      className="block text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                    >
                      <div className="max-w-[180px] truncate font-mono text-[11.5px] text-text">{task.task_id}</div>
                      <div className="text-[11px] text-text-4">
                        {task.task_type} · {task.media_type}
                      </div>
                    </button>
                  </td>
                  <td className="max-w-[150px] truncate px-3 py-2.5 text-text-2">{task.project_name}</td>
                  <td className="px-3 py-2.5"><StatusChip status={task.status} /></td>
                  <td className="px-3 py-2.5 text-text-2">{task.provider_id ?? "—"}</td>
                  <td className="w-[110px] px-3 py-2.5">
                    {task.progress === null ? (
                      <span className="text-text-4">—</span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <ProgressBar value={task.progress} label={`${task.task_id} ${task.progress}%`} />
                        <span className="font-mono text-[11px] tabular-nums text-text-3">{Math.round(task.progress)}%</span>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5"><CostCell task={task} /></td>
                  <td className="px-3 py-2.5 font-mono tabular-nums text-text-2">{formatMoney(task.provider_coins) ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-text-3">{formatTimestamp(task.queued_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager offset={offset} limit={PAGE_SIZE} total={data?.total ?? 0} onChange={setOffset} />

      <TaskDetailDrawer taskId={selected} onClose={() => setSelected(null)} onChanged={refresh} />
    </div>
  );
}
