import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import type { ReferenceDirectionReport, ReferenceDirectionRow, ReferenceDirectionShot } from "@/types/flow";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] text-text-4">{label}</dt>
      <dd className="text-[12.5px] leading-relaxed text-text">{children}</dd>
    </div>
  );
}

function cameraLabel(camera: ReferenceDirectionShot["camera"]): string {
  const extras = [
    camera.amplitude !== "medium" ? camera.amplitude : null,
    camera.speed !== "normal" ? camera.speed : null,
  ].filter(Boolean);
  return extras.length > 0 ? `${camera.type} (${extras.join(", ")})` : camera.type;
}

function UnitCard({ row, warned }: { row: ReferenceDirectionRow; warned: boolean }) {
  const { t } = useTranslation("dashboard");
  return (
    <li
      className={`flex flex-col gap-2.5 border-t py-3 first:border-t-0 ${warned ? "border-amber-400/40" : "border-hairline"}`}
      data-warned={warned || undefined}
      data-testid={`flow-dr-unit-${row.id}`}
    >
      <div className="flex items-start gap-2">
        <span className="shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px font-mono text-[10.5px] text-text-3">
          {row.id}
        </span>
        {row.unit_type && <span className="shrink-0 font-mono text-[10.5px] text-text-4">{row.unit_type}</span>}
        <p className="min-w-0 flex-1 whitespace-pre-wrap text-[12.5px] leading-relaxed text-text">
          {row.direction || row.brief || row.description}
        </p>
        {warned && <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" aria-hidden="true" />}
      </div>

      {row.direction ? null : (
        <>
      {row.shots.length > 0 && (
        <ol className="flex flex-col gap-1" aria-label={t("flow_dr_shots")}>
          {row.shots.map((shot, index) => (
            <li key={`${row.id}-shot-${index}`} className="flex items-start gap-2 text-[12px] leading-relaxed">
              <span className="w-12 shrink-0 font-mono text-[10.5px] text-text-3">{shot.at_seconds.toFixed(1)}s</span>
              <span className="min-w-0 flex-1 text-text-2">
                <span className="text-text">{shot.framing}</span>
                <span className="mx-1.5 font-mono text-[10.5px] text-accent-2">{cameraLabel(shot.camera)}</span>
                {shot.action}
                {shot.why_cut && (
                  <span className="ml-1.5 text-text-4">· {t("flow_dr_why_cut", { reason: shot.why_cut })}</span>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}

      {row.performance.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label={t("flow_dr_performance")}>
          {row.performance.map((item, index) => (
            <li key={`${item.character}-${index}`} className="flex items-start gap-2 text-[12px] leading-relaxed">
              <span className="shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px text-[10.5px] text-text-3">
                {item.character}
              </span>
              <span className="text-text-2">
                {item.note}
                {item.voice && (
                  <span className="ml-1.5 text-text-3">
                    · {t("flow_dr_voice")}: {item.voice}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {row.lines.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label={t("flow_dr_lines")}>
          {row.lines.map((line, index) => (
            <li key={`${row.id}-line-${index}`} className="flex items-start gap-2 text-[12px] leading-relaxed">
              <span className="w-12 shrink-0 font-mono text-[10.5px] text-text-3">
                {t("flow_dr_line_shot", { shot: line.shot })}
              </span>
              <span className="text-text">
                {line.speaker}：「{line.text}」
                {line.delivery && <span className="ml-1.5 text-text-3">({line.delivery})</span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      <dl className="grid grid-cols-2 gap-2">
        {row.sounds && <Labeled label={t("flow_dr_sounds")}>{row.sounds}</Labeled>}
        <Labeled label={t("flow_dr_music")}>{row.music || t("flow_dr_music_none")}</Labeled>
        {row.must_not_show && <Labeled label={t("flow_dr_must_not_show")}>{row.must_not_show}</Labeled>}
        {row.end_frame && <Labeled label={t("flow_dr_end_frame")}>{row.end_frame}</Labeled>}
        {row.continuity_in && <Labeled label={t("flow_dr_continuity_in")}>{row.continuity_in}</Labeled>}
        {row.continuity_out && <Labeled label={t("flow_dr_continuity_out")}>{row.continuity_out}</Labeled>}
      </dl>
        </>
      )}
    </li>
  );
}

/** 参考生视频路线的导演报告：逐单元的镜头表、表演与声音、台词落位、同期声与配乐、衔接，以及运镜分布。 */
export function ReferenceDirectionReportView({ report }: { report: ReferenceDirectionReport }) {
  const { t } = useTranslation("dashboard");
  const warnedIds = new Set(report.warnings.flatMap((w) => w.entry_ids));
  const moves = Object.entries(report.camera_distribution);
  const maxMove = Math.max(1, ...moves.map(([, n]) => n));

  return (
    <>
      <section className="rounded-[10px] border border-hairline px-3.5" style={CARD_STYLE}>
        <h3 className="sr-only">{t("flow_dr_rows")}</h3>
        <ul>
          {report.rows.map((row) => (
            <UnitCard key={row.id} row={row} warned={warnedIds.has(row.id)} />
          ))}
        </ul>
      </section>

      {moves.length > 0 && (
        <section className="rounded-[10px] border border-hairline px-3.5 py-3" style={CARD_STYLE}>
          <h3 className="mb-2 text-[12px] font-medium text-text-2">{t("flow_dr_camera_distribution")}</h3>
          <ul className="flex flex-col gap-1.5">
            {moves.map(([move, count]) => (
              <li key={move} className="flex items-center gap-2 text-[12px] text-text-2">
                <span className="w-28 shrink-0 truncate font-mono text-[11px]">{move}</span>
                <span
                  aria-hidden="true"
                  className="h-1.5 rounded-full bg-accent/60"
                  style={{ width: `${(count / maxMove) * 60}%` }}
                />
                <span className="font-mono text-[11px] text-text-3">{count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
