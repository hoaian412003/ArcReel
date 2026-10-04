import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { API } from "@/api";
import type { DirectionReview, DirectionReportRow } from "@/types/flow";
import { ReferenceDirectionReportView } from "./ReferenceDirectionReport";
import { errMsg } from "@/utils/async";
import { CARD_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] text-text-4">{label}</dt>
      <dd className="text-[12.5px] leading-relaxed text-text">{children}</dd>
    </div>
  );
}

function RowCard({ row, warned }: { row: DirectionReportRow; warned: boolean }) {
  const { t } = useTranslation("dashboard");
  const { camera } = row;
  return (
    <li
      className={`flex flex-col gap-2 border-t py-3 first:border-t-0 ${warned ? "border-amber-400/40" : "border-hairline"}`}
      data-warned={warned || undefined}
    >
      <div className="flex items-start gap-2">
        <span className="shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px font-mono text-[10.5px] text-text-3">
          {row.id}
        </span>
        <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-text">{row.description}</p>
        {warned && <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" aria-hidden="true" />}
      </div>
      <dl className="grid grid-cols-3 gap-2">
        <Labeled label={t("flow_dr_angle")}>{camera.angle}</Labeled>
        <Labeled label={t("flow_dr_shot_size")}>{camera.shot_size}</Labeled>
        <Labeled label={t("flow_dr_movement")}>{camera.movement}</Labeled>
      </dl>
      {camera.intent && <p className="text-[12.5px] italic leading-relaxed text-text-2">{camera.intent}</p>}
      {row.performance.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label={t("flow_dr_performance")}>
          {row.performance.map((p, i) => (
            <li key={`${p.character}-${i}`} className="flex items-start gap-2 text-[12px] leading-relaxed">
              <span className="shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px text-[10.5px] text-text-3">
                {p.character}
              </span>
              <span className="text-text-2">{p.note}</span>
            </li>
          ))}
        </ul>
      )}
      {(row.continuity_in || row.continuity_out) && (
        <dl className="grid grid-cols-2 gap-2">
          {row.continuity_in && <Labeled label={t("flow_dr_continuity_in")}>{row.continuity_in}</Labeled>}
          {row.continuity_out && <Labeled label={t("flow_dr_continuity_out")}>{row.continuity_out}</Labeled>}
        </dl>
      )}
    </li>
  );
}

/** 分镜导演报告：本集意图 / 基调 / 叙事弧线，逐镜头的机位与意图、表演、衔接，机位分布与警告。 */
export function DirectionReviewPanel({
  projectName,
  episode,
  version,
}: {
  projectName: string;
  episode: number;
  /** 变化时重新拉取（审阅决定落库后）。 */
  version: number;
}) {
  const { t } = useTranslation("dashboard");
  const target = `${projectName}:${episode}`;
  const [result, setResult] = useState<{ target: string; review: DirectionReview | null; error: string | null } | null>(
    null,
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    API.getDirectionReview(projectName, episode, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setResult({ target, review: res, error: null });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setResult({ target, review: null, error: errMsg(err) });
      });
    return () => controller.abort();
  }, [projectName, episode, target, version, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const current = result?.target === target ? result : null;
  const loading = current == null;
  const error = current?.error ?? null;
  const review = current?.review ?? null;

  if (loading) {
    return <div className="flex h-40 items-center justify-center text-text-4">{t("flow_dr_loading")}</div>;
  }
  if (error) {
    return (
      <div role="alert" className="flex h-40 flex-col items-center justify-center gap-3 text-center">
        <p className="text-[13px] font-medium text-text-2">{t("flow_dr_failed")}</p>
        <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{error}</p>
        <button type="button" onClick={retry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("flow_retry")}
        </button>
      </div>
    );
  }
  if (!review) return null;
  if (review.status === "not_applicable") {
    return <p className="text-[12.5px] text-text-3">{t("flow_dr_not_applicable")}</p>;
  }
  const report = review.report;
  if (review.status === "no_direction" || !report) {
    return <p className="text-[12.5px] text-text-3">{t("flow_dr_none")}</p>;
  }

  const warnedIds = new Set(report.warnings.flatMap((w) => w.entry_ids));
  const angles = report.route === "reference" ? [] : Object.entries(report.angle_distribution);
  const maxAngle = Math.max(1, ...angles.map(([, n]) => n));

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] text-text-3" data-testid="flow-dr-status">
        {t(`flow_dr_status_${review.status}`)}
        {review.note ? ` · ${t("flow_dr_note", { note: review.note })}` : ""}
      </p>

      {report.warnings.length > 0 && (
        <section
          aria-label={t("flow_dr_warnings")}
          className="rounded-[10px] border border-amber-400/40 bg-amber-400/5 px-3.5 py-3"
        >
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {t("flow_dr_warnings")}
          </h3>
          <ul className="flex flex-col gap-1.5">
            {report.warnings.map((w, i) => (
              <li key={`${w.code}-${i}`} className="text-[12px] leading-relaxed text-text-2">
                {w.entry_ids.length > 0 && (
                  <span className="mr-1.5 font-mono text-[10.5px] text-text-3">{w.entry_ids.join(", ")}</span>
                )}
                {w.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-[10px] border border-hairline px-3.5 py-3" style={CARD_STYLE}>
        <dl className="flex flex-col gap-2.5">
          <Labeled label={t("flow_dr_intent")}>{report.episode_intent}</Labeled>
          <Labeled label={t("flow_dr_tone")}>{report.tone}</Labeled>
          {report.arc.length > 0 && (
            <div className="flex flex-col gap-1">
              <dt className="text-[11px] text-text-4">{t("flow_dr_arc")}</dt>
              <dd>
                <ol className="flex flex-col gap-1">
                  {report.arc.map((a, i) => (
                    <li key={`${a.beat}-${i}`} className="text-[12.5px] leading-relaxed text-text">
                      {a.beat}
                      <span className="ml-1.5 font-mono text-[10.5px] text-text-4">
                        {Array.isArray(a.units) ? a.units.join(", ") : a.units}
                      </span>
                    </li>
                  ))}
                </ol>
              </dd>
            </div>
          )}
        </dl>
      </section>

      {report.route === "reference" ? (
        <ReferenceDirectionReportView report={report} />
      ) : (
        <section className="rounded-[10px] border border-hairline px-3.5" style={CARD_STYLE}>
          <h3 className="sr-only">{t("flow_dr_rows")}</h3>
          <ul>
            {report.rows.map((row) => (
              <RowCard key={row.id} row={row} warned={warnedIds.has(row.id)} />
            ))}
          </ul>
        </section>
      )}

      {angles.length > 0 && (
        <section className="rounded-[10px] border border-hairline px-3.5 py-3" style={CARD_STYLE}>
          <h3 className="mb-2 text-[12px] font-medium text-text-2">{t("flow_dr_angles")}</h3>
          <ul className="flex flex-col gap-1.5">
            {angles.map(([angle, count]) => (
              <li key={angle} className="flex items-center gap-2 text-[12px] text-text-2">
                <span className="w-24 shrink-0 truncate">{angle}</span>
                <span
                  aria-hidden="true"
                  className="h-1.5 rounded-full bg-accent/60"
                  style={{ width: `${(count / maxAngle) * 60}%` }}
                />
                <span className="font-mono text-[11px] text-text-3">{count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
