import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import type { Screenplay, ScreenplayScene } from "@/types/screenplay";
import { useAppStore } from "@/stores/app-store";
import { AskAgentButton } from "@/components/claude/AskAgentButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { errMsg } from "@/utils/async";
import { CARD_STYLE, GHOST_BTN_CLS, GHOST_BTN_LG_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";

const MIN_SECONDS = 1;
const MAX_SECONDS = 600;
const NEW_SCENE_SECONDS = 8;

type SceneDraft = Partial<Record<keyof ScreenplayScene, string>>;

/** 把草稿里与服务端值不同的字段收成一次 update 的 fields；时长必须是合法整数，否则返回 null。 */
function changedFields(scene: ScreenplayScene, draft: SceneDraft): Partial<ScreenplayScene> | null {
  const fields: Partial<ScreenplayScene> = {};
  for (const key of ["heading", "summary", "text"] as const) {
    const value = draft[key];
    if (value !== undefined && value !== scene[key]) fields[key] = value;
  }
  if (draft.estimated_seconds !== undefined) {
    const seconds = Number(draft.estimated_seconds);
    if (!Number.isInteger(seconds) || seconds < MIN_SECONDS || seconds > MAX_SECONDS) return null;
    if (seconds !== scene.estimated_seconds) fields.estimated_seconds = seconds;
  }
  return fields;
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes > 0 ? `${minutes}:${String(rest).padStart(2, "0")}` : `0:${String(rest).padStart(2, "0")}`;
}

function SceneEditor({
  number,
  scene,
  draft,
  busy,
  structureLocked,
  canDelete,
  onChange,
  onSave,
  onDelete,
}: {
  number: number;
  scene: ScreenplayScene;
  draft: SceneDraft | undefined;
  busy: boolean;
  /** 其他场有未保存的修改时，增删场次会让序号错位，先锁住。 */
  structureLocked: boolean;
  canDelete: boolean;
  onChange: (field: keyof ScreenplayScene, value: string) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const fields = draft ? changedFields(scene, draft) : {};
  const invalid = fields === null;
  const dirty = fields !== null && Object.keys(fields).length > 0;
  const value = (key: keyof ScreenplayScene) => draft?.[key] ?? String(scene[key]);
  const idPrefix = `screenplay-scene-${number}`;

  return (
    <li
      className="flex flex-col gap-2.5 rounded-[10px] border border-hairline px-3.5 py-3"
      style={CARD_STYLE}
      data-testid={`screenplay-scene-${number}`}
      data-dirty={dirty || undefined}
    >
      <div className="flex items-center gap-2">
        <span className="shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px font-mono text-[10.5px] text-text-3">
          {t("screenplay_scene_label", { number })}
        </span>
        <input
          id={`${idPrefix}-heading`}
          aria-label={t("screenplay_field_heading")}
          className={INPUT_CLS}
          value={value("heading")}
          disabled={busy}
          onChange={(e) => onChange("heading", e.target.value)}
        />
        <label className="flex shrink-0 items-center gap-1.5 text-[11.5px] text-text-3" htmlFor={`${idPrefix}-seconds`}>
          {t("screenplay_field_seconds")}
          <input
            id={`${idPrefix}-seconds`}
            type="number"
            min={MIN_SECONDS}
            max={MAX_SECONDS}
            aria-invalid={invalid || undefined}
            className={`${INPUT_CLS} !w-20`}
            value={value("estimated_seconds")}
            disabled={busy}
            onChange={(e) => onChange("estimated_seconds", e.target.value)}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-[11px] text-text-4" htmlFor={`${idPrefix}-summary`}>
        {t("screenplay_field_summary")}
        <textarea
          id={`${idPrefix}-summary`}
          rows={2}
          className={INPUT_CLS}
          value={value("summary")}
          disabled={busy}
          onChange={(e) => onChange("summary", e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-text-4" htmlFor={`${idPrefix}-text`}>
        {t("screenplay_field_text")}
        <textarea
          id={`${idPrefix}-text`}
          rows={6}
          className={`${INPUT_CLS} font-mono text-[12.5px] leading-relaxed`}
          value={value("text")}
          disabled={busy}
          onChange={(e) => onChange("text", e.target.value)}
        />
      </label>
      {invalid && (
        <p role="alert" className="text-[11.5px] text-amber-400">
          {t("screenplay_seconds_invalid", { min: MIN_SECONDS, max: MAX_SECONDS })}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          className={GHOST_BTN_CLS}
          disabled={busy || structureLocked || !canDelete}
          onClick={onDelete}
          aria-label={t("screenplay_delete_scene", { number })}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          {t("screenplay_delete_scene_short")}
        </button>
        <button type="button" className={GHOST_BTN_CLS} disabled={busy || !dirty || invalid} onClick={onSave}>
          <Save className="h-3.5 w-3.5" aria-hidden="true" />
          {t("screenplay_save_scene")}
        </button>
      </div>
    </li>
  );
}

/** 编剧剧本审阅：标题 / 看点、逐场可编辑的剧本（标题、剧情、预计时长、正文）与本集预计总时长。 */
export function ScreenplayReviewPanel({
  projectName,
  episode,
  version,
  onLoaded,
  onApproved,
}: {
  projectName: string;
  episode: number;
  /** 变化时重新拉取（审阅决定落库后）。 */
  version: number;
  /** 每次拿到最新剧本状态时回报，供外层用审阅过的指纹发起「通过」。 */
  onLoaded?: (screenplay: Screenplay) => void;
  /** 审阅通过后通知宿主刷新看板或关闭抽屉；审批本身由本面板完成。 */
  onApproved?: (screenplay: Screenplay) => void;
}) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const target = `${projectName}:${episode}`;
  const [result, setResult] = useState<{ target: string; screenplay: Screenplay | null; error: string | null } | null>(
    null,
  );
  const [attempt, setAttempt] = useState(0);
  const [drafts, setDrafts] = useState<Record<number, SceneDraft>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    API.getScreenplay(projectName, episode, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setResult({ target, screenplay: res, error: null });
        setDrafts({});
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setResult({ target, screenplay: null, error: errMsg(err) });
      });
    return () => controller.abort();
  }, [projectName, episode, target, version, attempt]);

  const current = result?.target === target ? result : null;
  const screenplay = current?.screenplay ?? null;

  useEffect(() => {
    if (screenplay) onLoaded?.(screenplay);
  }, [screenplay, onLoaded]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const dirtyIndexes = useMemo(
    () =>
      Object.entries(drafts)
        .filter(([index, draft]) => {
          const scene = screenplay?.scenes[Number(index)];
          if (!scene) return false;
          const fields = changedFields(scene, draft);
          return fields === null || Object.keys(fields).length > 0;
        })
        .map(([index]) => Number(index)),
    [drafts, screenplay],
  );

  const apply = useCallback(
    async (operations: Parameters<typeof API.patchScreenplay>[3]) => {
      if (!screenplay?.fingerprint) return;
      setBusy(true);
      try {
        const next = await API.patchScreenplay(projectName, episode, screenplay.fingerprint, operations);
        setResult({ target, screenplay: next, error: null });
        setDrafts({});
        pushToast(t("screenplay_saved_toast"), "success");
      } catch (err) {
        if (err instanceof ApiRequestError && err.status === 409) {
          pushToast(t("screenplay_conflict_toast"), "warning");
          retry();
        } else {
          pushToast(t("screenplay_save_failed", { message: errMsg(err) }), "error");
        }
      } finally {
        setBusy(false);
      }
    },
    [screenplay, projectName, episode, target, pushToast, retry, t],
  );

  const approve = useCallback(async () => {
    if (!screenplay?.fingerprint || dirtyIndexes.length > 0 || screenplay.status === "approved") return;
    setBusy(true);
    try {
      const next = await API.approveScreenplay(projectName, episode, screenplay.fingerprint);
      setResult({ target, screenplay: next, error: null });
      pushToast(t("screenplay_approved_toast"), "success");
      onApproved?.(next);
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 409) {
        pushToast(t("screenplay_conflict_toast"), "warning");
        retry();
      } else {
        pushToast(t("screenplay_approve_failed", { message: errMsg(err) }), "error");
      }
    } finally {
      setBusy(false);
    }
  }, [screenplay, dirtyIndexes.length, projectName, episode, target, pushToast, t, onApproved, retry]);

  if (current == null) {
    return <div className="flex h-40 items-center justify-center text-text-4">{t("screenplay_loading")}</div>;
  }
  if (current.error) {
    return (
      <div role="alert" className="flex h-40 flex-col items-center justify-center gap-3 text-center">
        <p className="text-[13px] font-medium text-text-2">{t("screenplay_failed")}</p>
        <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{current.error}</p>
        <button type="button" onClick={retry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("flow_retry")}
        </button>
      </div>
    );
  }
  if (!screenplay || screenplay.status === "none") {
    return (
      <div>
        <p className="text-[12.5px] text-text-3">{t("screenplay_none")}</p>
        <AskAgentButton projectName={projectName} action={{ action: "create_screenplay", episode }} />
      </div>
    );
  }

  const structureLocked = dirtyIndexes.length > 0;

  return (
    <div className="flex flex-col gap-3">
      <header className="border-b border-hairline pb-3">
        <h2 className="text-[15px] font-semibold text-text">{t("screenplay_report_title")}</h2>
        <p className="mt-1 text-[12px] leading-relaxed text-text-3">{t("screenplay_report_hint")}</p>
      </header>

      <p className="text-[12px] text-text-3" data-testid="screenplay-status">
        {t(`screenplay_status_${screenplay.status}`)}
      </p>

      <section className="rounded-[10px] border border-hairline px-3.5 py-3" style={CARD_STYLE}>
        <h3 className="text-[14px] font-medium text-text">{screenplay.title}</h3>
        <p className="mt-1 text-[12.5px] leading-relaxed text-text-2">{screenplay.logline}</p>
        <p className="mt-2 font-mono text-[11.5px] text-text-3" data-testid="screenplay-total">
          {t("screenplay_total", {
            seconds: screenplay.total_seconds,
            clock: formatDuration(screenplay.total_seconds),
            count: screenplay.scenes.length,
          })}
        </p>
      </section>

      <ol className="flex flex-col gap-2.5" aria-label={t("screenplay_scenes")}>
        {screenplay.scenes.map((scene, index) => (
          <SceneEditor
            key={`${screenplay.fingerprint}-${index}`}
            number={index + 1}
            scene={scene}
            draft={drafts[index]}
            busy={busy}
            structureLocked={structureLocked}
            canDelete={screenplay.scenes.length > 1}
            onChange={(field, value) =>
              setDrafts((cur) => ({ ...cur, [index]: { ...cur[index], [field]: value } }))
            }
            onSave={() => {
              const fields = changedFields(scene, drafts[index] ?? {});
              if (fields && Object.keys(fields).length > 0) void apply([{ op: "update", index, fields }]);
            }}
            onDelete={() => void apply([{ op: "delete", index }])}
          />
        ))}
      </ol>

      <div className="flex justify-start">
        <button
          type="button"
          className={GHOST_BTN_LG_CLS}
          disabled={busy || structureLocked}
          onClick={() =>
            void apply([
              {
                op: "insert",
                index: screenplay.scenes.length,
                scene: {
                  heading: t("screenplay_new_scene_heading"),
                  summary: t("screenplay_new_scene_summary"),
                  estimated_seconds: NEW_SCENE_SECONDS,
                  text: t("screenplay_new_scene_text"),
                },
              },
            ])
          }
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          {t("screenplay_add_scene")}
        </button>
      </div>

      <footer className="flex flex-col items-end gap-2 border-t border-hairline pt-3">
        {structureLocked && <p className="text-[11.5px] text-amber-400">{t("screenplay_unsaved_changes")}</p>}
        <PrimaryButton
          disabled={busy || structureLocked || screenplay.status === "approved"}
          leadingIcon={<Check className="h-3.5 w-3.5" aria-hidden="true" />}
          onClick={() => void approve()}
        >
          {busy
            ? t("screenplay_approving")
            : screenplay.status === "approved"
              ? t("screenplay_status_approved")
              : t("screenplay_approve")}
        </PrimaryButton>
      </footer>
    </div>
  );
}
