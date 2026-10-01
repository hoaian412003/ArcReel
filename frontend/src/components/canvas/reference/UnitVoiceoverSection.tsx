import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Download, Film, Loader2, Play, Save } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { errMsg } from "@/utils/async";
import { DubPreviewPlayer, type DubPreviewClip } from "./DubPreviewPlayer";
import { useDubTasks } from "./use-dub-tasks";
import type { DubGender, DubLinePatch, UnitDub, UnitDubLine } from "@/types";

export interface UnitVoiceoverSectionProps {
  projectName: string;
  episode: number;
  unitId: string;
  /** 单元正文与台词条数变化时重新拉取时间线。 */
  refreshKey: string;
}

interface LineDraft {
  text?: string;
  gender?: DubGender;
  /** 输入框里的开始时间文本；空串即清除覆盖、回到顺序排布。 */
  start?: string;
}

function formatSeconds(value: number): string {
  return (Math.round(value * 10) / 10).toFixed(1);
}

function parseStart(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/**
 * 单元的越南语配音面板：开始时间 / 性别 / 配音台词的可编辑表格、逐行试听，
 * 以及压低原声后叠加配音的预览与成片导出。
 */
export function UnitVoiceoverSection({ projectName, episode, unitId, refreshKey }: UnitVoiceoverSectionProps) {
  const { t } = useTranslation("dashboard");
  const dubTasks = useDubTasks(projectName);
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const [dub, setDub] = useState<UnitDub | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<number, LineDraft>>({});
  const [saving, setSaving] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [rendered, setRendered] = useState<{ path: string; at: number } | null>(null);
  const lineAudioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    let cancelled = false;
    API.getUnitDub(projectName, episode, unitId)
      .then((result) => {
        if (cancelled) return;
        setDub(result);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setDub(null);
        setLoadError(errMsg(e));
      });
    return () => {
      cancelled = true;
    };
  }, [projectName, episode, unitId, refreshKey, dubTasks.version]);

  const lines = useMemo(() => dub?.lines ?? [], [dub]);
  const dirty = Object.keys(drafts).length > 0;

  const updateDraft = useCallback((index: number, patch: LineDraft) => {
    setDrafts((current) => ({ ...current, [index]: { ...current[index], ...patch } }));
  }, []);

  const handleSave = async () => {
    if (!dub || !dirty || saving) return;
    const payload: DubLinePatch[] = lines.map((line) => {
      const draft = drafts[line.index];
      return {
        index: line.index,
        speaker: line.speaker,
        gender: draft?.gender ?? line.gender,
        text_vi: (draft?.text ?? line.text_vi).trim() || line.text_vi,
        start_seconds: draft?.start !== undefined ? parseStart(draft.start) : line.start_override,
      };
    });
    setSaving(true);
    try {
      await patchUnit(projectName, episode, unitId, { dub_lines: payload });
      setDrafts({});
      setDub(await API.getUnitDub(projectName, episode, unitId));
      useAppStore.getState().pushToast(t("dub_saved_toast"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("dub_save_failed", { error: errMsg(e) }), "error");
    } finally {
      setSaving(false);
    }
  };

  const handleRender = async () => {
    if (rendering) return;
    setRendering(true);
    try {
      const result = await API.renderUnitDub(projectName, episode, unitId);
      setRendered({ path: result.file_path, at: Date.now() });
      useAppStore.getState().pushToast(t("dub_render_done"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("dub_render_failed", { error: errMsg(e) }), "error");
    } finally {
      setRendering(false);
    }
  };

  const playLine = (line: UnitDubLine) => {
    const audio = lineAudioRef.current;
    if (!audio || !line.audio_path) return;
    audio.src = API.getFileUrl(projectName, line.audio_path, line.duration_seconds);
    void audio.play().catch(() => undefined);
  };

  const clips: DubPreviewClip[] = lines.flatMap((line) =>
    line.audio_path
      ? [
          {
            index: line.index,
            url: API.getFileUrl(projectName, line.audio_path, line.duration_seconds),
            startSeconds: line.start_seconds,
            durationSeconds: line.duration_seconds,
            text: line.text_vi,
          },
        ]
      : [],
  );
  const videoUrl = dub?.video_path ? API.getFileUrl(projectName, dub.video_path) : null;

  if (loadError) {
    return (
      <p role="alert" className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-[11.5px] text-red-300">
        {t("dub_load_failed", { error: loadError })}
      </p>
    );
  }
  if (!dub) return null;

  return (
    <section
      aria-label={t("dub_section_title")}
      className="flex flex-col gap-2.5 rounded-lg border border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] p-3"
    >
      <div className="flex items-center gap-1.5">
        <h3 className="text-xs font-semibold text-[var(--color-text-2)]">{t("dub_section_title")}</h3>
        <span className="flex-1" />
        {dirty && (
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="focus-ring inline-flex items-center gap-1 rounded-md border border-[var(--color-hairline)] px-2 py-0.5 text-[11px] text-[var(--color-text-2)] hover:text-[var(--color-text)] disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Save className="h-3 w-3" aria-hidden="true" />
            )}
            {t("dub_save")}
          </button>
        )}
      </div>

      {lines.length === 0 ? (
        <p className="text-[11.5px] text-[var(--color-text-4)]">{t("dub_empty")}</p>
      ) : (
        <table className="w-full table-fixed border-collapse text-[11.5px]">
          <thead>
            <tr className="text-left text-[10.5px] text-[var(--color-text-4)]">
              <th scope="col" className="w-16 pb-1 font-medium">{t("dub_col_start")}</th>
              <th scope="col" className="w-20 pb-1 font-medium">{t("dub_col_gender")}</th>
              <th scope="col" className="pb-1 font-medium">{t("dub_col_text")}</th>
              <th scope="col" className="w-7 pb-1"><span className="sr-only">{t("dub_col_audio")}</span></th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => {
              const draft = drafts[line.index];
              const startValue = draft?.start ?? formatSeconds(line.start_seconds);
              return (
                <tr key={line.index} className="align-top">
                  <td className="pr-1.5 pt-0.5">
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      inputMode="decimal"
                      value={startValue}
                      onChange={(e) => updateDraft(line.index, { start: e.target.value })}
                      aria-label={t("dub_start_label", { index: line.index + 1 })}
                      className={`focus-ring w-full rounded border border-[var(--color-hairline-soft)] bg-transparent px-1 py-0.5 font-mono tabular-nums ${
                        line.start_override === null && draft?.start === undefined
                          ? "text-[var(--color-text-3)]"
                          : "text-[var(--color-text)]"
                      }`}
                    />
                  </td>
                  <td className="pr-1.5 pt-0.5">
                    <select
                      value={draft?.gender ?? line.gender}
                      onChange={(e) => updateDraft(line.index, { gender: e.target.value as DubGender })}
                      aria-label={t("dub_gender_label", { index: line.index + 1 })}
                      className="focus-ring w-full rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.20_0.011_265)] px-1 py-0.5 text-[var(--color-text-2)]"
                    >
                      <option value="male">{t("dub_gender_male")}</option>
                      <option value="female">{t("dub_gender_female")}</option>
                    </select>
                  </td>
                  <td className="pr-1.5 pt-0.5">
                    <textarea
                      rows={2}
                      value={draft?.text ?? line.text_vi}
                      onChange={(e) => updateDraft(line.index, { text: e.target.value })}
                      aria-label={t("dub_text_label", { index: line.index + 1 })}
                      lang="vi"
                      className="focus-ring w-full resize-y rounded border border-[var(--color-hairline-soft)] bg-transparent px-1.5 py-1 leading-snug text-[var(--color-text-2)]"
                    />
                    <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10.5px] text-[var(--color-text-4)]">
                      {line.speaker && <span translate="no">{line.speaker}</span>}
                      {line.stale && (
                        <span className="inline-flex items-center gap-0.5 text-amber-300">
                          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                          {t("dub_line_stale")}
                        </span>
                      )}
                      {!line.audio_path && <span>{t("dub_line_no_audio")}</span>}
                    </div>
                  </td>
                  <td className="pt-0.5">
                    {line.audio_path && (
                      <button
                        type="button"
                        onClick={() => playLine(line)}
                        aria-label={t("dub_play_line", { index: line.index + 1 })}
                        className="focus-ring rounded p-1 text-[var(--color-text-3)] hover:text-[var(--color-text)]"
                      >
                        <Play className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {/* 逐行试听的音频无画面，字幕即上方表格里的台词文本 */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={lineAudioRef} className="hidden" />

      {dub.overflow_seconds > 0.05 && (
        <p className="flex items-start gap-1.5 text-[11.5px] text-amber-200">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          {t("dub_overflow", { seconds: formatSeconds(dub.overflow_seconds) })}
        </p>
      )}

      {videoUrl && clips.length > 0 ? (
        <div className="flex flex-col gap-2">
          <DubPreviewPlayer videoUrl={videoUrl} clips={clips} originalVolume={dub.original_volume} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleRender()}
              disabled={rendering}
              className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-2 py-1 text-[11.5px] text-[var(--color-text-2)] hover:text-[var(--color-text)] disabled:opacity-60"
            >
              {rendering ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Film className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {t("dub_render")}
            </button>
            {rendered && (
              <a
                href={API.getFileUrl(projectName, rendered.path, rendered.at)}
                download
                className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-[var(--color-accent)] hover:underline"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                {t("dub_download")}
              </a>
            )}
          </div>
        </div>
      ) : (
        lines.length > 0 && <p className="text-[11.5px] text-[var(--color-text-4)]">{t("dub_preview_pending")}</p>
      )}
    </section>
  );
}
