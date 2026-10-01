import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, RefreshCw } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import {
  H3_PROMPT_SECTIONS,
  type H3PromptSections,
  type H3PromptStatus,
  type ReferenceVideoUnit,
} from "@/types";

const STATUS_COLOR: Record<H3PromptStatus, string> = {
  current: "var(--color-text-3)",
  stale: "var(--color-warn, #e0a23b)",
  missing: "var(--color-danger, #e5484d)",
};

const EMPTY_SECTIONS: H3PromptSections = {
  subject_definitions: "",
  summary: "",
  retention_analysis: "",
  detailed_description: "",
  overall_soundscape: "",
  non_diegetic_music: "",
};

function sectionsOf(unit: ReferenceVideoUnit): H3PromptSections {
  if (!unit.h3_prompt) return EMPTY_SECTIONS;
  return Object.fromEntries(H3_PROMPT_SECTIONS.map((name) => [name, unit.h3_prompt?.[name] ?? ""])) as H3PromptSections;
}

export interface H3PromptControlsProps {
  unit: ReferenceVideoUnit;
  projectName: string;
  episode: number;
}

/**
 * H3 提示词档下单元卡片上的状态、重新生成与手写入口。项目不使用 H3 提示词档时（列表响应没有状态表）不渲染。
 * 状态以服务端指纹比对为准；重新生成只重写六段、不动单元正文。
 */
export function H3PromptControls({ unit, projectName, episode }: H3PromptControlsProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const status = useReferenceVideoStore(
    (s) => s.h3PromptStatusByEpisode[referenceVideoCacheKey(projectName, episode)]?.[unit.unit_id],
  );
  const regenerate = useReferenceVideoStore((s) => s.regenerateH3Prompts);
  const save = useReferenceVideoStore((s) => s.saveH3Prompt);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<H3PromptSections>(EMPTY_SECTIONS);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!status) return null;

  const handleRegenerate = async () => {
    setBusy(true);
    const { pushToast } = useAppStore.getState();
    try {
      pushToast(t("h3_prompt_regenerate_queued", { id: unit.unit_id }), "info");
      await regenerate(projectName, episode, [unit.unit_id]);
    } catch (e) {
      pushToast(t("h3_prompt_regenerate_failed", { reason: errMsg(e) }), "error");
    } finally {
      setBusy(false);
    }
  };

  const openEditor = () => {
    setDraft(sectionsOf(unit));
    setError(null);
    setEditing(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await save(projectName, episode, unit.unit_id, draft);
      useAppStore.getState().pushToast(t("h3_prompt_saved", { id: unit.unit_id }), "success");
      setEditing(false);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <span
        className="inline-flex items-center gap-1 text-[10px]"
        style={{ color: STATUS_COLOR[status] }}
        title={t(`h3_prompt_status_${status}_hint`)}
      >
        {t("h3_prompt_label")} · {t(`h3_prompt_status_${status}`)}
      </span>
      <button
        type="button"
        onClick={() => void handleRegenerate()}
        disabled={busy}
        title={t("h3_prompt_regenerate")}
        className="focus-ring inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors hover:bg-white/5 disabled:cursor-default disabled:opacity-40"
        style={{ color: "var(--color-text-3)" }}
      >
        <RefreshCw aria-hidden className={`h-3 w-3 ${busy ? "animate-spin" : ""}`} />
        {busy ? t("h3_prompt_regenerating") : t("h3_prompt_regenerate")}
      </button>
      <button
        type="button"
        onClick={openEditor}
        title={t("h3_prompt_edit")}
        className="focus-ring inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors hover:bg-white/5"
        style={{ color: "var(--color-text-3)" }}
      >
        <Pencil aria-hidden className="h-3 w-3" />
        {t("h3_prompt_edit")}
      </button>
      <GlassModal open={editing} onClose={() => setEditing(false)} labelledBy={titleId} widthClassName="w-full max-w-3xl">
        <div
          className="flex items-start justify-between gap-4 px-5 py-4"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <div className="min-w-0">
            <h2 id={titleId} className="text-[14px] font-semibold tracking-tight" style={{ color: "var(--color-text)" }}>
              {t("h3_prompt_edit_title", { id: unit.unit_id })}
            </h2>
            <p className="mt-1 text-[11px] leading-[1.5]" style={{ color: "var(--color-text-4)" }}>
              {t("h3_prompt_edit_hint")}
            </p>
          </div>
          <ModalCloseButton onClick={() => setEditing(false)} />
        </div>
        <div className="flex max-h-[65vh] flex-col gap-3 overflow-y-auto px-5 py-4">
          {H3_PROMPT_SECTIONS.map((name) => (
            <label key={name} className="flex flex-col gap-1">
              <span className="font-mono text-[11px]" style={{ color: "var(--color-text-3)" }} translate="no">
                {name}
              </span>
              <textarea
                value={draft[name]}
                onChange={(e) => setDraft((d) => ({ ...d, [name]: e.target.value }))}
                rows={name === "detailed_description" ? 10 : 4}
                spellCheck={false}
                className="focus-ring w-full resize-y rounded-md bg-black/20 p-2 font-mono text-[11px] leading-[1.5]"
                style={{ color: "var(--color-text-2)", border: "1px solid var(--color-hairline-soft)" }}
              />
            </label>
          ))}
          {error ? (
            <p role="alert" className="text-[11px]" style={{ color: "var(--color-danger, #e5484d)" }}>
              {error}
            </p>
          ) : null}
        </div>
        <div
          className="flex justify-end gap-2 px-5 py-3"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="focus-ring rounded-md px-3 py-1 text-[12px] hover:bg-white/5"
            style={{ color: "var(--color-text-3)" }}
          >
            {t("h3_prompt_cancel")}
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || H3_PROMPT_SECTIONS.some((name) => !draft[name].trim())}
            className="focus-ring rounded-md px-3 py-1 text-[12px] disabled:opacity-40"
            style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }}
          >
            {saving ? t("h3_prompt_saving") : t("h3_prompt_save")}
          </button>
        </div>
      </GlassModal>
    </>
  );
}

export interface H3RegenerateAllButtonProps {
  projectName: string;
  episode: number;
}

/**
 * 本集全部单元的「重新生成 H3 提示词」入口：只在 H3 提示词档的项目、且本集有单元时渲染。
 * 单元正文不动，只重写六段载荷。
 */
export function H3RegenerateAllButton({ projectName, episode }: H3RegenerateAllButtonProps) {
  const { t } = useTranslation("dashboard");
  const statuses = useReferenceVideoStore(
    (s) => s.h3PromptStatusByEpisode[referenceVideoCacheKey(projectName, episode)],
  );
  const regenerate = useReferenceVideoStore((s) => s.regenerateH3Prompts);
  const [busy, setBusy] = useState(false);

  if (!statuses || Object.keys(statuses).length === 0) return null;

  const handleClick = async () => {
    setBusy(true);
    const { pushToast } = useAppStore.getState();
    try {
      pushToast(t("h3_prompt_regenerate_queued", { id: `E${episode}` }), "info");
      await regenerate(projectName, episode);
    } catch (e) {
      pushToast(t("h3_prompt_regenerate_failed", { reason: errMsg(e) }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={busy}
      className="focus-ring inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] transition-colors hover:bg-white/5 disabled:cursor-default disabled:opacity-40"
      style={{ color: "var(--color-text-3)" }}
    >
      <RefreshCw aria-hidden className={`h-3 w-3 ${busy ? "animate-spin" : ""}`} />
      {busy ? t("h3_prompt_regenerating") : t("h3_prompt_regenerate_all")}
    </button>
  );
}
