import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { usePolling } from "@/hooks/usePolling";
import { formatTimestamp } from "@/utils/queue-format";
import { Pager, StaleBanner, StatePanel } from "./queue-ui";

const PAGE_SIZE = 25;
const ACTIONS = [
  "enqueue_accepted",
  "enqueue_rejected",
  "cancel",
  "mark_failed",
  "limits_changed",
  "reconciled",
] as const;

export function QueueAuditTab() {
  const { t } = useTranslation("queue");
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [project, setProject] = useState("");
  const [offset, setOffset] = useState(0);
  const debounced = useDebouncedValue({ user, project }, 300);

  useEffect(() => {
    // 过滤条件变化后回到第一页
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOffset(0);
  }, [action, debounced]);

  const { data, loading, error, refresh } = usePolling(
    (signal) =>
      API.listQueueAudit(
        { action, user_id: debounced.user, project: debounced.project, limit: PAGE_SIZE, offset },
        { signal },
      ),
    [action, debounced, offset],
  );
  const items = data?.items ?? [];

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select aria-label={t("filter.action")} value={action} onChange={(e) => setAction(e.target.value)} className={`${INPUT_CLS} !w-auto`}>
          <option value="">{t("filter.all_actions")}</option>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{t(`audit.action.${a}`)}</option>
          ))}
        </select>
        <input aria-label={t("filter.user")} placeholder={t("filter.user")} value={user} onChange={(e) => setUser(e.target.value)} className={`${INPUT_CLS} !w-[160px]`} />
        <input aria-label={t("filter.project")} placeholder={t("filter.project")} value={project} onChange={(e) => setProject(e.target.value)} className={`${INPUT_CLS} !w-[160px]`} />
      </div>

      <StaleBanner error={items.length > 0 ? error : null} />
      <StatePanel loading={loading && !data} error={error} empty={items.length === 0} emptyText={t("audit.empty")} onRetry={refresh} />

      {items.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-hairline">
          <table className="w-full min-w-[720px] text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-hairline text-[11px] uppercase tracking-wider text-text-4">
                {["col.time", "col.actor", "col.action", "col.project", "col.task", "col.detail"].map((key) => (
                  <th key={key} scope="col" className="px-3 py-2.5 font-medium">{t(key)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => (
                <tr key={entry.id} className="border-b border-hairline-soft align-top last:border-0">
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-text-3">{formatTimestamp(entry.at)}</td>
                  <td className="px-3 py-2.5 text-text-2">{entry.actor}</td>
                  <td className="px-3 py-2.5">
                    <span className="rounded-full border border-hairline bg-bg-grad-a/50 px-2 py-0.5 text-[11px] text-text-2">
                      {t(`audit.action.${entry.action}`)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-text-2">{entry.project_name ?? "—"}</td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-text-3">
                    <div className="max-w-[160px] truncate">{entry.task_id ?? "—"}</div>
                    {entry.task_type && <div className="text-text-4">{entry.task_type}</div>}
                  </td>
                  <td className="max-w-[260px] px-3 py-2.5 font-mono text-[11px] text-text-3">
                    <div className="line-clamp-3 break-all" title={JSON.stringify(entry.detail)}>
                      {Object.keys(entry.detail).length === 0 ? "—" : JSON.stringify(entry.detail)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager offset={offset} limit={PAGE_SIZE} total={data?.total ?? 0} onChange={setOffset} />
    </div>
  );
}
