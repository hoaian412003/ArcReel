import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Lock, RotateCcw, Save, Wrench } from "lucide-react";
import type {
  DramaNormalizedScript,
  DramaSceneContent,
  NarrationScriptPlanDraft,
  NarrationScriptPlanSegment,
  ScriptReviewQuarantine,
  ScriptReviewState,
  Utterance,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { VideoModelUnresolvedNotice } from "@/components/shared/VideoModelUnresolvedNotice";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import {
  ACCENT_BUTTON_STYLE,
  ACCENT_BTN_CLS,
  CARD_STYLE,
  GHOST_BTN_CLS,
  GHOST_BTN_LG_CLS,
} from "@/components/ui/darkroom-tokens";
import { sumItemDuration } from "@/utils/script-shape";
import { GenerateScriptPlanEmpty } from "./GenerateScriptPlanEmpty";
import { UtteranceListEditor } from "./UtteranceListEditor";

interface ScriptReviewGateProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
  videoModelUnresolved?: boolean;
  /** 切到本集时间线；确认后的只读态据此给出去时间线修改的入口，未提供时不渲染入口。 */
  onOpenTimeline?: () => void;
}

const SECTION_LABEL_STYLE: React.CSSProperties = {
  color: "var(--color-text-4)",
  letterSpacing: "0.08em",
  fontFamily: "var(--font-mono)",
};

/** 两条 script_plan 变体（drama / narration）的可编辑草稿联合。 */
type ReviewDraft = DramaNormalizedScript | NarrationScriptPlanDraft;

/** 本面板承接 drama / narration 两个变体的内容，reference_video 变体不会路由到这里。 */
function selectReviewContent(state: ScriptReviewState): ReviewDraft | null {
  return (state.content ?? null) as ReviewDraft | null;
}

/** 只读正文：保留换行，空值不渲染。 */
function ReadOnlyText({ text, className = "" }: { text: string; className?: string }) {
  if (!text) return null;
  return <p className={`whitespace-pre-wrap text-[12.5px] leading-relaxed ${className}`}>{text}</p>;
}

function ReadOnlyUtterances({ utterances }: { utterances: Utterance[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <ul className="flex flex-col gap-1.5">
      {utterances.map((u, i) => (
        <li key={String(i)} className="flex items-start gap-2 text-[12.5px] leading-relaxed">
          <span className="mt-0.5 shrink-0 rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px text-[10.5px] text-text-3">
            {u.kind === "dialogue" ? u.speaker : t("utterance_kind_voiceover")}
          </span>
          <span className={u.kind === "voiceover" ? "italic text-text-2" : "text-text"}>{u.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** 一个场景一行：序号 + 画面描述，下方是台词。只露出审阅要看的两样内容。 */
function SceneRow({
  index,
  children,
}: {
  index: number;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3 border-t border-hairline py-3 first:border-t-0">
      <span className="w-5 shrink-0 pt-0.5 text-right font-mono text-[11px] text-text-4">{index + 1}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">{children}</div>
    </li>
  );
}

function DramaSceneCard({
  index,
  scene,
  disabled,
  readOnly,
  onChange,
}: {
  index: number;
  scene: DramaSceneContent;
  disabled: boolean;
  readOnly: boolean;
  onChange: (patch: Partial<DramaSceneContent>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <SceneRow index={index}>
      {readOnly ? (
        <ReadOnlyText text={scene.scene_description} className="text-text" />
      ) : (
        <AutoTextarea
          value={scene.scene_description}
          disabled={disabled}
          onChange={(scene_description) => onChange({ scene_description })}
          placeholder={t("review_scene_description_placeholder")}
          aria-label={t("review_scene_description_label")}
        />
      )}
      {readOnly ? (
        <ReadOnlyUtterances utterances={scene.utterances} />
      ) : (
        <UtteranceListEditor
          utterances={scene.utterances}
          disabled={disabled}
          onChange={(utterances: Utterance[]) => onChange({ utterances })}
        />
      )}
    </SceneRow>
  );
}

function NarrationSegmentCard({
  index,
  segment,
  disabled,
  readOnly,
  onChange,
}: {
  index: number;
  segment: NarrationScriptPlanSegment;
  disabled: boolean;
  readOnly: boolean;
  onChange: (patch: Partial<NarrationScriptPlanSegment>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <SceneRow index={index}>
      {readOnly ? (
        <ReadOnlyText text={segment.novel_text} className="text-text" />
      ) : (
        <AutoTextarea
          value={segment.novel_text}
          onChange={(novel_text) => onChange({ novel_text })}
          placeholder={t("review_novel_text_placeholder")}
          aria-label={t("review_novel_text_label")}
          disabled={disabled}
        />
      )}
    </SceneRow>
  );
}

/** 本集摘要：取分集规划写下的剧情要点，没有时整块不渲染。 */
function EpisodeSummary({ episode }: { episode: number }) {
  const { t } = useTranslation("dashboard");
  const beats = useProjectsStore(
    (s) => s.currentProjectData?.episodes.find((e) => e.episode === episode)?.outline?.story_beats,
  );
  const text = beats?.filter(Boolean).join(" ");
  if (!text) return null;
  return (
    <section className="rounded-[10px] border border-hairline px-3.5 py-3" style={CARD_STYLE}>
      <h3 className="mb-1 text-[12px] font-medium text-text-2">{t("review_summary_title", { episode })}</h3>
      <p className="text-[12.5px] leading-relaxed text-text">{text}</p>
    </section>
  );
}

/**
 * 待修复草稿在场时的只读呈现：违约逐条列出 + 草稿正文原样展示。
 *
 * 不复用上面两个可编辑卡片渲染草稿：草稿正是给 Agent 手改的那一份，字段可能缺失或类型不对，
 * 按可编辑卡片的字段契约渲染要么崩、要么把缺失字段补成用户没写过的值——而渲染崩掉恰好发生在
 * 用户最需要看到面板的时候。原样展示 + 逐条违约足以回答「哪里不对、正在改的是什么」，修复由
 * Agent 在草稿上完成，晋升后本面板自动回到可编辑态。
 */
function QuarantinePanel(props: { quarantine: ScriptReviewQuarantine; onRequestFix: () => void }) {
  const { t } = useTranslation("dashboard");
  const violations = props.quarantine.violations;
  const rawDraft = props.quarantine.content;
  return (
    <div className="flex flex-col gap-2.5">
      <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-[10.5px]" style={SECTION_LABEL_STYLE}>
            {t("review_quarantined_violations_label", { count: violations.length })}
          </span>
          <button type="button" onClick={props.onRequestFix} className={GHOST_BTN_CLS}>
            <Wrench className="h-3.5 w-3.5" />
            {t("review_request_fix")}
          </button>
        </div>
        {violations.length === 0 ? (
          <p className="text-[11.5px] text-text-3">{t("review_quarantined_no_violations")}</p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {violations.map((violation, i) => (
              <li key={violation.code + "-" + String(i)} className="text-[11.5px] leading-relaxed text-text-2">
                {violation.label && (
                  <span className="mr-1.5 rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[10.5px] text-text-3">
                    {violation.label}
                  </span>
                )}
                {violation.message}
              </li>
            ))}
          </ol>
        )}
      </article>

      {rawDraft != null && (
        <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
          <p className="mb-2 text-[10.5px]" style={SECTION_LABEL_STYLE}>
            {t("review_quarantined_draft_label")}
          </p>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] text-text-3">
            {JSON.stringify(rawDraft, null, 2)}
          </pre>
        </article>
      )}
    </div>
  );
}

/**
 * script_plan→prompt_authoring web 内容确认面板：把 script_plan 结构化中间态在网页结构化呈现、可手动 / Agent 编辑，
 * 用户显式确认后才放行 prompt_authoring 视觉生成。drama（utterances + source_text）与 narration
 * （novel_text）共用本面板；reference_video 变体的专属面板见 `ReferenceScriptPlanPreviewPanel`。
 *
 * 待修复草稿在场时整面板转只读（见 `QuarantinePanel`）：正式内容此刻仍是上一版，编辑与确认
 * 都无意义——确认端点本就按同一判据拒绝。确认之后脚本规划只读，卡片不渲染编辑控件，指引到时间线修改。
 */
export function ScriptReviewGate({ projectName, episode, contentMode, videoModelUnresolved, onOpenTimeline }: ScriptReviewGateProps) {
  const { t } = useTranslation("dashboard");
  const standaloneCapabilities = useModelCapabilities({ projectName, enabled: videoModelUnresolved === undefined });
  const modelUnresolved = videoModelUnresolved ?? standaloneCapabilities.videoModelUnresolved;
  const pushToast = useAppStore((s) => s.pushToast);
  const [overwriteOpen, setOverwriteOpen] = useState(false);

  const handleConfirmed = useCallback(() => {
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [pushToast, t]);

  const {
    state,
    draft,
    setDraft,
    dirty,
    loading,
    loadError,
    saving,
    busy,
    retry: handleRetry,
    save: handleSave,
    confirm: handleConfirm,
    confirming,
  } = useScriptReviewDraft<ReviewDraft>({
    projectName,
    episode,
    selectContent: selectReviewContent,
    onConfirmed: handleConfirmed,
  });

  const updateDramaScene = (index: number, patch: Partial<DramaSceneContent>) => {
    setDraft((prev) => {
      if (!prev || !("scenes" in prev)) return prev;
      return { ...prev, scenes: prev.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  const updateNarrationSegment = (index: number, patch: Partial<NarrationScriptPlanSegment>) => {
    setDraft((prev) => {
      if (!prev || !("segments" in prev)) return prev;
      return { ...prev, segments: prev.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  // 待修复草稿的处置只能由 Agent 在草稿上完成（正式文件写禁、晋升要经工具全量重判），故这里
  // 把逐条违约预填进对话输入框，用户一句话就能把上下文完整交给它，不必自己转述。
  const handleRequestFix = useCallback(() => {
    const violations = state?.quarantine?.violations ?? [];
    const docType = contentMode === "drama" ? "drama_script_plan" : "narration_script_plan";
    // 重算已无违约、但待修复草稿仍在场（Agent 已改对内容、尚未调晋升工具）：不能报「0 处违约
    // 待修复」再让用户去改一份已经没问题的东西，正确的下一步是请 Agent 直接晋升。
    const report =
      violations.length === 0
        ? t("dashboard:review_fix_request_promote_prefill", { episode, docType })
        : [
            t("dashboard:review_fix_request_prefill_header", { episode, count: violations.length, docType }),
            ...violations.map((v, i) => String(i + 1) + ". " + v.message),
          ].join("\n");
    useAssistantStore.getState().setInput(report);
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [state, episode, contentMode, t]);

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:loading_script_plan")}</div>;
  }

  // 加载错误态：区别于「无 script_plan 产物」空态，展示错误信息 + 重试入口。
  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-medium text-text-2">{t("dashboard:review_load_failed")}</p>
          {loadError.message && (
            <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{loadError.message}</p>
          )}
        </div>
        <button type="button" onClick={handleRetry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("dashboard:review_retry")}
        </button>
      </div>
    );
  }

  const status = state?.status ?? "no_script_plan";
  const quarantine = state?.quarantine ?? null;
  if (status === "no_script_plan" || (draft == null && quarantine == null)) {
    return <GenerateScriptPlanEmpty projectName={projectName} episode={episode} />;
  }

  // 待修复草稿在场：正式内容此刻仍是上一版，可编辑态会让用户改一份不会被消费的内容，且确认
  // 端点本就按同一判据拒绝。故整面板转只读，编辑与确认一并锁住。
  const quarantined = quarantine != null;
  // 已确认的脚本规划只读：保存端点按同一判据拒绝，内容修改改在时间线上做。
  const confirmed = status === "confirmed" && !quarantined;
  // 该集已有正式脚本：确认会整份覆盖它，确认按钮改呈 danger，点击先列出后果再确认。
  const overwrite = confirmed ? null : (state?.script_overwrite ?? null);
  // 已确认但该集没有正式脚本（迁移转换失败或文件被删）：确认仍可用，重新确认即转出正式脚本。
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = quarantined || (confirmed && !scriptMissing);
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  const confirmBlockedHint = quarantined
    ? t("dashboard:review_confirm_blocked_quarantined")
    : videoModelBlocked
      ? t("dashboard:review_video_model_unresolved_hint")
      : undefined;

  return (
    <div className="flex flex-col gap-3">
      {/* 内容确认状态条 + 确认动作 */}
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? (
            <AlertTriangle className="h-4 w-4 text-amber-400" />
          ) : confirmed ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          ) : (
            <Clock className="h-4 w-4 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined
                ? t("dashboard:review_status_quarantined")
                : confirmed
                  ? t("dashboard:review_status_confirmed")
                  : t("dashboard:review_status_pending")}
            </span>
            <span className="text-[11px] text-text-4">
              {quarantined
                ? t("dashboard:review_quarantined_hint")
                : scriptMissing
                  ? t("dashboard:review_script_missing_hint")
                  : confirmed
                    ? t("dashboard:review_confirmed_hint")
                    : overwrite
                    ? t("dashboard:review_overwrite_hint")
                    : t("dashboard:review_pending_hint")}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {confirmed && onOpenTimeline && (
            <button type="button" onClick={onOpenTimeline} className={GHOST_BTN_CLS}>
              <ArrowRight className="h-3.5 w-3.5" />
              {t("dashboard:review_open_timeline")}
            </button>
          )}
          {dirty && !quarantined && !confirmed && (
            <button type="button" onClick={voidPromise(handleSave)} disabled={busy} className={GHOST_BTN_CLS}>
              <Save className="h-3.5 w-3.5" />
              {saving ? t("common:saving") : t("dashboard:review_save_action")}
            </button>
          )}
          {overwrite ? (
            <PrimaryButton
              tone="danger"
              onClick={() => setOverwriteOpen(true)}
              disabled={busy || quarantined || videoModelBlocked}
              title={confirmBlockedHint}
              leadingIcon={quarantined ? <Lock className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
            >
              {confirming ? t("dashboard:review_confirming") : t("dashboard:review_overwrite_action")}
            </PrimaryButton>
          ) : (
            <button
              type="button"
              onClick={voidPromise(() => handleConfirm())}
              disabled={busy || confirmLocked || videoModelBlocked}
              title={confirmBlockedHint}
              className={ACCENT_BTN_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {confirmLocked ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              {confirming
                ? t("dashboard:review_confirming")
                : scriptMissing
                  ? t("dashboard:review_rematerialize_action")
                  : confirmed
                    ? t("dashboard:review_confirmed_badge")
                    : t("dashboard:review_confirm_action")}
            </button>
          )}
        </div>
      </header>

      {videoModelBlocked && <VideoModelUnresolvedNotice projectName={projectName} />}

      {overwrite && (
        <ScriptOverwriteConfirmDialog
          open={overwriteOpen}
          overwrite={overwrite}
          loading={confirming}
          // 框在能力请求返回之前就可能被打开，之后答复模型无法解析：框内的确认按钮与触发它的
          // 那颗按钮同一判据，否则这里还能提交一次注定被服务端拒绝的确认。
          confirmDisabled={videoModelBlocked}
          onConfirm={async () => {
            // 失败（如确认期间该集被并发写入）时框保持打开，呈现刷新后的覆盖清单。
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      {!quarantined && <EpisodeSummary episode={episode} />}

      {/* 本集合计与项目目标的对比；未设目标时不渲染，超出只提示不阻断确认 */}
      {!quarantined && (
        <EpisodeDurationSummary
          totalSeconds={sumItemDuration(
            draft == null ? [] : "scenes" in draft ? draft.scenes : draft.segments,
          )}
          targetSeconds={state?.episode_target_duration ?? null}
        />
      )}

      {/* 结构化中间态卡片；待修复草稿在场时改为只读的违约面板 */}
      {quarantined ? (
        <QuarantinePanel quarantine={quarantine} onRequestFix={handleRequestFix} />
      ) : (
        <section className="rounded-[10px] border border-hairline px-3.5" style={CARD_STYLE}>
          <ul>
          {contentMode === "drama" && draft != null && "scenes" in draft
            ? draft.scenes.map((scene, i) => (
                <DramaSceneCard
                  index={i}
                  key={scene.scene_id || i}
                  scene={scene}
                  disabled={busy}
                  readOnly={confirmed}
                  onChange={(patch) => updateDramaScene(i, patch)}
                />
              ))
            : null}
          {contentMode === "narration" && draft != null && "segments" in draft
            ? draft.segments.map((segment, i) => (
                <NarrationSegmentCard
                  index={i}
                  key={segment.segment_id || i}
                  segment={segment}
                  disabled={busy}
                  readOnly={confirmed}
                  onChange={(patch) => updateNarrationSegment(i, patch)}
                />
              ))
            : null}
          </ul>
        </section>
      )}
    </div>
  );
}
