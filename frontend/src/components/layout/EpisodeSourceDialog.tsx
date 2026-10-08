import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadEpisodeSource } from "@/actions/assets";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import type { EpisodeMeta } from "@/types";

const ACCEPTED_EXTENSIONS = ".txt,.md,.docx,.epub,.pdf";

function EpisodeSourceForm({
  projectName,
  episodes,
  titleId,
  initialEpisode,
  onClose,
  onUploaded,
}: {
  projectName: string;
  episodes: readonly EpisodeMeta[];
  titleId: string;
  initialEpisode?: number;
  onClose: () => void;
  onUploaded?: (episode: number) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const numberId = useId();
  const nameId = useId();
  const fileId = useId();
  const contentId = useId();
  const nextEpisode = episodes.reduce((max, ep) => Math.max(max, ep.episode), 0) + 1;
  const [episode, setEpisode] = useState(String(initialEpisode ?? nextEpisode));
  const [title, setTitle] = useState(() => episodes.find((ep) => ep.episode === initialEpisode)?.title ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const episodeNumber = Number(episode);
  const validNumber = Number.isInteger(episodeNumber) && episodeNumber >= 1;
  const replacing = validNumber && episodes.some((ep) => ep.episode === episodeNumber);
  const hasInput = file !== null || content.trim().length > 0;

  const submit = async () => {
    if (!validNumber || !hasInput || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await uploadEpisodeSource(projectName, episodeNumber, {
        ...(file ? { file } : { content }),
        title,
      });
      onUploaded?.(result.episode);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
          {t(replacing ? "dashboard:episode_source_dialog_title_replace" : "dashboard:episode_source_dialog_title_new")}
        </h2>
        <p className="text-[12.5px] leading-relaxed text-text-3">{t("dashboard:episode_source_dialog_desc")}</p>

        <div className="grid grid-cols-[96px_1fr] gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={numberId} className="text-[12px] text-text-2">
              {t("dashboard:episode_source_number_label")}
            </label>
            <input
              id={numberId}
              type="number"
              min={1}
              step={1}
              value={episode}
              onChange={(e) => setEpisode(e.target.value)}
              disabled={submitting}
              className={INPUT_CLS}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={nameId} className="text-[12px] text-text-2">
              {t("dashboard:episode_source_title_label")}
            </label>
            <input
              id={nameId}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("dashboard:episode_source_title_placeholder")}
              disabled={submitting}
              className={INPUT_CLS}
            />
          </div>
        </div>

        {replacing ? (
          <p className="text-[12px] leading-relaxed" style={{ color: "var(--color-warm)" }} role="note">
            {t("dashboard:episode_source_replace_warning", { episode: episodeNumber })}
          </p>
        ) : null}

        <div className="flex flex-col gap-1.5">
          <label htmlFor={fileId} className="text-[12px] text-text-2">
            {t("dashboard:episode_source_file_label")}
          </label>
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              id={fileId}
              type="file"
              accept={ACCEPTED_EXTENSIONS}
              disabled={submitting}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="min-w-0 flex-1 text-[12px] text-text-3"
            />
            {file ? (
              <SecondaryButton
                size="sm"
                onClick={() => {
                  setFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                disabled={submitting}
              >
                {t("dashboard:episode_source_clear_file")}
              </SecondaryButton>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor={contentId} className="text-[12px] text-text-2">
            {t("dashboard:episode_source_paste_label")}
          </label>
          <textarea
            id={contentId}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={t("dashboard:episode_source_paste_placeholder")}
            rows={8}
            disabled={submitting || file !== null}
            className={INPUT_CLS}
          />
          {file ? (
            <p className="text-[11px] text-text-4">{t("dashboard:episode_source_file_wins_hint")}</p>
          ) : null}
        </div>

        {error ? (
          <p role="alert" className="text-[12px]" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        ) : null}

        <div className="mt-1 flex justify-end gap-2">
          <SecondaryButton onClick={onClose} disabled={submitting}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={submitting || !validNumber || !hasInput}>
            {submitting ? t("dashboard:episode_source_submitting") : t("dashboard:episode_source_submit")}
          </PrimaryButton>
        </div>
      </form>
  );
}

/**
 * 上传（或替换）一集源文的弹窗：drama 项目不经分集规划，每一集的原文由用户逐集提供。
 * 成功即登记分集并由服务端入队资产分析；集号落在已有集上视为替换。
 * 表单只在打开时挂载：预填集号按打开瞬间的账本取一次，关闭即丢弃草稿。
 */
export function EpisodeSourceDialog({
  open,
  projectName,
  episodes,
  initialEpisode,
  onClose,
  onUploaded,
}: {
  open: boolean;
  projectName: string;
  episodes: readonly EpisodeMeta[];
  /** 预填集号：缺省取下一集；替换入口传该集号。 */
  initialEpisode?: number;
  onClose: () => void;
  /** 上传成功（已登记并刷新项目）后调用，早于 onClose。 */
  onUploaded?: (episode: number) => void;
}) {
  const titleId = useId();
  return (
    <GlassModal open={open} onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-lg">
      {open ? (
        <EpisodeSourceForm
          projectName={projectName}
          episodes={episodes}
          titleId={titleId}
          initialEpisode={initialEpisode}
          onClose={onClose}
          onUploaded={onUploaded}
        />
      ) : null}
    </GlassModal>
  );
}
