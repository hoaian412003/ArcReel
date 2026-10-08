import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { splitNovelIntoEpisodes } from "@/actions/assets";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import type { NovelSplitResult } from "@/types";

/** 分集源文是 ``episode_N.txt``，不是可切分的整本小说。 */
const DERIVED_EPISODE_FILE = /^episode_\d+\.txt$/;

type PreviewState =
  | { key: string; status: "ready"; result: NovelSplitResult }
  | { key: string; status: "error"; message: string };

function warningText(t: (key: string, options?: Record<string, unknown>) => string, warning: string): string {
  const [code, detail] = warning.split(":");
  return t(`dashboard:novel_split_warning_${code}`, { chapters: detail ?? "", defaultValue: code });
}

function NovelSplitForm({
  projectName,
  titleId,
  onClose,
}: {
  projectName: string;
  titleId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const sourceId = useId();
  const perEpisodeId = useId();
  const [sources, setSources] = useState<string[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [perEpisode, setPerEpisode] = useState("1");
  const [replace, setReplace] = useState(false);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.listFiles(projectName)
      .then((res) => {
        if (controller.signal.aborted) return;
        const names = (res.files.source ?? []).map((f) => f.name).filter((n) => !DERIVED_EPISODE_FILE.test(n));
        setSources(names);
        setChosen((current) => current || names[0] || "");
      })
      .catch(() => {
        if (!controller.signal.aborted) setSources([]);
      });
    return () => controller.abort();
  }, [projectName]);

  const count = Number(perEpisode);
  const validCount = Number.isInteger(count) && count >= 1 && count <= 50;
  const previewKey = `${chosen}::${count}`;

  useEffect(() => {
    if (!chosen || !validCount) return;
    let disposed = false;
    API.previewNovelSplit(projectName, { source: chosen, chaptersPerEpisode: count })
      .then((result) => {
        if (!disposed) setPreview({ key: previewKey, status: "ready", result });
      })
      .catch((err: unknown) => {
        if (!disposed) {
          setPreview({ key: previewKey, status: "error", message: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      disposed = true;
    };
  }, [projectName, chosen, count, validCount, previewKey]);

  const current = preview?.key === previewKey ? preview : null;
  const result = current?.status === "ready" ? current.result : null;
  const needsReplace = (result?.conflicts.length ?? 0) > 0;
  const canSubmit = result !== null && result.episodes.length > 0 && (!needsReplace || replace) && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await splitNovelIntoEpisodes(projectName, { source: chosen, chaptersPerEpisode: count, replace });
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form
      className="flex flex-col gap-3 px-6 pb-6 pt-5"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-text">
        {t("dashboard:novel_split_title")}
      </h2>
      <p className="text-[12.5px] leading-relaxed text-text-3">{t("dashboard:novel_split_desc")}</p>

      {sources !== null && sources.length === 0 ? (
        <p className="text-[12.5px]" style={{ color: "var(--color-warm)" }} role="note">
          {t("dashboard:novel_split_no_source")}
        </p>
      ) : (
        <div className="grid grid-cols-[1fr_120px] gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={sourceId} className="text-[12px] text-text-2">
              {t("dashboard:novel_split_source_label")}
            </label>
            <select
              id={sourceId}
              value={chosen}
              onChange={(e) => setChosen(e.target.value)}
              disabled={submitting || sources === null}
              className={INPUT_CLS}
            >
              {(sources ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={perEpisodeId} className="text-[12px] text-text-2">
              {t("dashboard:novel_split_per_episode_label")}
            </label>
            <input
              id={perEpisodeId}
              type="number"
              min={1}
              max={50}
              step={1}
              value={perEpisode}
              onChange={(e) => setPerEpisode(e.target.value)}
              disabled={submitting}
              className={INPUT_CLS}
            />
          </div>
        </div>
      )}

      {current?.status === "error" ? (
        <p role="alert" className="text-[12px]" style={{ color: "var(--color-danger)" }}>
          {current.message}
        </p>
      ) : null}

      {result ? (
        <div className="flex flex-col gap-2">
          <p className="text-[12.5px] text-text-2" data-testid="novel-split-summary">
            {t("dashboard:novel_split_summary", {
              chapters: result.chapter_count,
              episodes: result.episodes.length,
            })}
          </p>
          {result.warnings.map((w) => (
            <p key={w} className="text-[11.5px]" style={{ color: "var(--color-warm)" }} role="note">
              {warningText(t, w)}
            </p>
          ))}
          <ol
            className="num max-h-56 list-none overflow-y-auto rounded-lg p-2 text-[12px]"
            style={{ border: "1px solid var(--color-hairline)", background: "oklch(0.18 0.010 265 / 0.5)" }}
          >
            {result.episodes.map((ep) => (
              <li key={ep.episode} className="flex items-baseline gap-2 py-0.5">
                <span className="w-9 shrink-0 text-text-4">E{ep.episode}</span>
                <span className="min-w-0 flex-1 truncate text-text-2">{ep.title}</span>
                <span className="shrink-0 text-text-4">{ep.chars.toLocaleString()}</span>
              </li>
            ))}
          </ol>
          {needsReplace ? (
            <label className="flex items-start gap-2 text-[12px]" style={{ color: "var(--color-warm)" }}>
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
                disabled={submitting}
                className="mt-0.5"
              />
              <span>{t("dashboard:novel_split_replace_label", { episodes: result.conflicts.join(", ") })}</span>
            </label>
          ) : null}
        </div>
      ) : null}

      {submitError ? (
        <p role="alert" className="text-[12px]" style={{ color: "var(--color-danger)" }}>
          {submitError}
        </p>
      ) : null}

      <div className="mt-1 flex justify-end gap-2">
        <SecondaryButton onClick={onClose} disabled={submitting}>
          {t("common:cancel")}
        </SecondaryButton>
        <PrimaryButton type="submit" disabled={!canSubmit}>
          {submitting ? t("dashboard:novel_split_submitting") : t("dashboard:novel_split_submit")}
        </PrimaryButton>
      </div>
    </form>
  );
}

/**
 * 把整本小说按章节标题（正则，不调模型）切成分集的弹窗：先预览切分结果与告警，确认后一次建好全部集，
 * 随后服务端入队资产分析。表单只在打开时挂载，关闭即丢弃预览。
 */
export function NovelSplitDialog({
  open,
  projectName,
  onClose,
}: {
  open: boolean;
  projectName: string;
  onClose: () => void;
}) {
  const titleId = useId();
  return (
    <GlassModal open={open} onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-lg">
      {open ? <NovelSplitForm projectName={projectName} titleId={titleId} onClose={onClose} /> : null}
    </GlassModal>
  );
}
