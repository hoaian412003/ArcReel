import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { usePolling } from "@/hooks/usePolling";
import { formatAgeSeconds, formatMoney, formatTimestamp, laneUsagePercent } from "@/utils/queue-format";
import { QueueCard, StaleBanner, StatePanel } from "./queue-ui";

export function QueueOverviewTab() {
  const { t } = useTranslation("queue");
  const { data, loading, error, refresh } = usePolling((signal) => API.getQueueOverview({ signal }), []);

  if (!data) {
    return <StatePanel loading={loading} error={error} empty emptyText={t("empty")} onRetry={refresh} />;
  }

  const { worker, counts, lanes, cost_24h: cost } = data;
  const age = formatAgeSeconds(data.oldest_queued_age_seconds);
  const countItems = [
    { key: "queued", value: counts.queued },
    { key: "running", value: counts.running },
    { key: "succeeded_24h", value: counts.succeeded_24h },
    { key: "failed_24h", value: counts.failed_24h },
    { key: "cancelled_24h", value: counts.cancelled_24h },
  ] as const;

  return (
    <div className="space-y-4">
      <StaleBanner error={error} />
      <div className="grid gap-4 md:grid-cols-3">
        <QueueCard kicker="queue · worker" title={t("worker.title")}>
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="h-2.5 w-2.5 rounded-full"
              style={{ background: worker.online ? "var(--color-accent-2)" : "var(--color-danger)" }}
            />
            <span
              className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                worker.online
                  ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-300"
                  : "border-red-500/40 bg-red-500/10 text-red-300"
              }`}
            >
              {worker.online ? t("worker.online") : t("worker.offline")}
            </span>
            <span className="text-[12px] text-text-3">{t(`worker.mode.${worker.mode}`)}</span>
          </div>
          {worker.owner_id ? (
            <dl className="mt-3 space-y-1 text-[12px]">
              <div className="flex justify-between gap-3">
                <dt className="text-text-4">{t("worker.owner")}</dt>
                <dd className="truncate font-mono text-text-2">{worker.owner_id}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-text-4">{t("worker.lease_until")}</dt>
                <dd className="font-mono text-text-2">{formatTimestamp(worker.lease_until)}</dd>
              </div>
            </dl>
          ) : (
            <p className="mt-3 text-[12px] text-text-4">{t("worker.none")}</p>
          )}
        </QueueCard>

        <QueueCard kicker="queue · oldest" title={t("oldest.title")}>
          <div className="font-editorial text-[34px] leading-none tabular-nums text-text">{age ?? "—"}</div>
          {age === null && <p className="mt-2 text-[12px] text-text-4">{t("oldest.none")}</p>}
        </QueueCard>

        <QueueCard kicker="queue · cost 24h" title={t("cost.title")}>
          {cost.totals.length === 0 && cost.provider_coins === 0 ? (
            <p className="text-[12px] text-text-4">{t("cost.none")}</p>
          ) : (
            <ul className="space-y-1 font-mono text-[14px] tabular-nums text-text">
              {cost.totals.map((total) => (
                <li key={total.currency}>{formatMoney(total.amount, total.currency)}</li>
              ))}
              <li className="text-text-2">
                {cost.provider_coins} <span className="text-[11px] text-text-4">{t("cost.coins")}</span>
              </li>
            </ul>
          )}
        </QueueCard>
      </div>

      <QueueCard kicker="queue · counts" title={t("counts.title")}>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {countItems.map(({ key, value }) => (
            <div key={key} className="flex flex-col-reverse rounded-xl border border-hairline-soft bg-bg-grad-a/40 px-3 py-2.5">
              <dd className="font-editorial text-[26px] leading-none tabular-nums text-text">{value}</dd>
              <dt className="mb-1 text-[11.5px] text-text-3">{t(`counts.${key}`)}</dt>
            </div>
          ))}
        </dl>
      </QueueCard>

      <QueueCard kicker="queue · lanes" title={t("lanes.title")}>
        {lanes.length === 0 ? (
          <p className="text-[12px] text-text-4">{t("lanes.empty")}</p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {lanes.map((lane) => {
              const pct = laneUsagePercent(lane.running, lane.limit);
              const label = t("lanes.bar_label", { provider: lane.provider_id, media: lane.media_type });
              return (
                <li key={`${lane.provider_id}/${lane.media_type}`} className="rounded-xl border border-hairline-soft bg-bg-grad-a/40 px-3 py-2.5">
                  <div className="mb-1.5 flex items-baseline justify-between gap-2">
                    <span className="truncate text-[12.5px] text-text">
                      {lane.provider_id}
                      <span className="ml-1.5 font-mono text-[10px] uppercase tracking-wider text-text-4">{lane.media_type}</span>
                    </span>
                    <span className="shrink-0 font-mono text-[11px] tabular-nums text-text-3">
                      {t("lanes.usage", { running: lane.running, limit: lane.limit })}
                    </span>
                  </div>
                  <ProgressBar value={pct} label={label} barClassName={pct >= 100 ? "bg-red-400" : undefined} />
                  <div className="mt-1.5 text-[11px] text-text-4">{t("lanes.queued", { count: lane.queued })}</div>
                </li>
              );
            })}
          </ul>
        )}
      </QueueCard>
    </div>
  );
}
