import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, FileText, Loader2, RefreshCw } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import type { FlowCard, FlowColumn, ReviewTask } from "@/types/flow";
import type { Screenplay } from "@/types/screenplay";
import type { ScriptOverwrite } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useClaudeStore } from "@/stores/claude-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { FlowCardDrawer } from "../flow/FlowCardDrawer";
import { FlowRedoDialog } from "../flow/FlowRedoDialog";

function overwriteFromError(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const overwrite = (diagnostic as { script_overwrite?: unknown }).script_overwrite;
  return overwrite != null && typeof overwrite === "object" ? (overwrite as ScriptOverwrite) : null;
}

function asFlowCard(task: ReviewTask): FlowCard {
  const column = task.kind === "direction" ? "direction_review" : "script_plan_review";
  const nextAction = task.kind === "screenplay" ? "review_screenplay" : task.kind === "script_plan" ? "confirm_script_plan" : "review_direction";
  return { episode: task.episode, title: task.title, column, next_action: nextAction, blocked: task.blockers.length > 0 };
}

const HUMAN_COLUMN: FlowColumn = { id: "review", kind: "human" };

export function ReviewInbox({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const contentMode = useProjectsStore((s) => s.currentProjectData?.content_mode);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const settledTasks = useTasksStore((s) => s.stats.succeeded + s.stats.failed + s.stats.cancelled);
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyEpisode, setBusyEpisode] = useState<number | null>(null);
  const [summarizing, setSummarizing] = useState<Set<string>>(new Set());
  const [refreshTick, setRefreshTick] = useState(0);
  const [reviewVersion, setReviewVersion] = useState(0);
  const [redoTarget, setRedoTarget] = useState<ReviewTask | null>(null);
  const [overwriteTarget, setOverwriteTarget] = useState<{ task: ReviewTask; overwrite: ScriptOverwrite } | null>(null);
  const reviewedScreenplay = useRef<Record<number, string>>({});

  const refresh = useCallback(() => {
    setReviewVersion((n) => n + 1);
    setRefreshTick((n) => n + 1);
  }, []);

  const runSummary = useCallback(async (task: ReviewTask, signal?: AbortSignal) => {
    setSummarizing((current) => new Set(current).add(task.id));
    try {
      const updated = await API.createReviewSummary(projectName, task.kind, task.episode, { signal });
      if (!signal?.aborted) setTasks((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (err) {
      if (!signal?.aborted) pushToast(t("review_summary_failed", { message: errMsg(err) }), "error");
    } finally {
      if (!signal?.aborted) setSummarizing((current) => { const next = new Set(current); next.delete(task.id); return next; });
    }
  }, [projectName, pushToast, t]);

  useEffect(() => {
    const controller = new AbortController();
    API.getReviewTasks(projectName, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setTasks(response.tasks);
        setError(null);
        setLoading(false);
        for (const task of response.tasks) if (!task.summary) void runSummary(task, controller.signal);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) { setError(errMsg(err)); setLoading(false); }
      });
    return () => controller.abort();
  }, [projectName, refreshTick, runSummary, settledTasks, snapshotRevision]);

  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const reportFailure = useCallback(
    (err: unknown) => pushToast(t("flow_action_failed", { message: errMsg(err) }), "error"),
    [pushToast, t],
  );

  const approve = useCallback(async (task: ReviewTask, overwriteRevision?: string) => {
    setBusyEpisode(task.episode);
    try {
      if (task.kind === "screenplay") {
        const fingerprint = reviewedScreenplay.current[task.episode] || task.fingerprint;
        await API.approveScreenplay(projectName, task.episode, fingerprint);
      } else if (task.kind === "script_plan") {
        await API.confirmScriptReview(projectName, task.episode, { overwriteRevision });
      } else {
        await API.decideDirectionReview(projectName, task.episode, { decision: "approve" });
      }
      pushToast(t("flow_approved_toast", { episode: task.episode }), "success");
      setSelectedId(null);
      refresh();
      return true;
    } catch (err) {
      const overwrite = overwriteRevision === undefined ? overwriteFromError(err) : null;
      if (overwrite) setOverwriteTarget({ task, overwrite }); else reportFailure(err);
      return false;
    } finally { setBusyEpisode(null); }
  }, [projectName, pushToast, refresh, reportFailure, t]);

  const submitRedo = useCallback(async (note: string) => {
    if (!redoTarget) return;
    setBusyEpisode(redoTarget.episode);
    try {
      await API.decideDirectionReview(projectName, redoTarget.episode, { decision: "redo", ...(note ? { note } : {}) });
      await useClaudeStore.getState().ask(projectName, { action: "redo_episode", episode: redoTarget.episode, note });
      setRedoTarget(null); setSelectedId(null); refresh();
    } catch (err) { reportFailure(err); }
    finally { setBusyEpisode(null); }
  }, [projectName, redoTarget, refresh, reportFailure]);

  if (loading) return <div className="flex h-full items-center justify-center text-text-4">{t("review_inbox_loading")}</div>;
  if (error) return <div role="alert" className="flex h-full flex-col items-center justify-center gap-3"><p className="text-text-2">{t("review_inbox_load_failed")}</p><p className="font-mono text-[11px] text-text-4">{error}</p><button className={GHOST_BTN_LG_CLS} onClick={refresh}>{t("flow_retry")}</button></div>;

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <header className="flex items-end justify-between border-b border-hairline px-6 py-5">
        <div><div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">Review Inbox</div><h1 className="display-serif mt-1 text-[22px] font-semibold text-text">{t("review_inbox_title")}</h1><p className="mt-1 text-[12px] text-text-3">{t("review_inbox_hint")}</p></div>
        <span className="font-mono text-[11px] text-text-4">{t("review_inbox_count", { count: tasks.length })}</span>
      </header>
      {tasks.length === 0 ? (
        <div className="grid flex-1 place-items-center px-6 text-center"><div><CheckCircle2 className="mx-auto h-8 w-8 text-emerald-400/80"/><p className="mt-3 text-[14px] text-text-2">{t("review_inbox_empty")}</p><p className="mt-1 text-[12px] text-text-4">{t("review_inbox_empty_hint")}</p></div></div>
      ) : (
        <ol className="min-h-0 flex-1 overflow-y-auto">
          {tasks.map((task) => {
            const isSummarizing = summarizing.has(task.id);
            return <li key={task.id} className="border-b border-hairline-soft">
              <button type="button" onClick={() => setSelectedId(task.id)} className="group grid w-full gap-4 px-6 py-5 text-left transition-colors hover:bg-white/[0.025] md:grid-cols-[150px_minmax(0,1fr)_24px]">
                <div><span className="font-mono text-[10px] uppercase tracking-[0.13em] text-accent-2">{t(`review_kind_${task.kind}`)}</span><p className="mt-1 text-[12px] text-text-3">{t("flow_card_label", { episode: task.episode })}</p><p className="mt-0.5 truncate text-[13px] text-text">{task.title}</p></div>
                <div className="min-w-0">{isSummarizing ? <p className="flex items-center gap-2 text-[12px] text-text-4"><Loader2 className="h-3.5 w-3.5 animate-spin"/>{t("review_summary_generating")}</p> : task.summary ? <p className="whitespace-pre-line text-[12.5px] leading-6 text-text-2">{task.summary}</p> : <span className="inline-flex items-center gap-2 text-[12px] text-accent-2"><RefreshCw className="h-3.5 w-3.5"/>{t("review_summary_retry")}</span>}{task.blockers.length > 0 && <p className="mt-3 inline-flex items-center gap-1.5 text-[11px] text-amber-400"><AlertTriangle className="h-3.5 w-3.5"/>{t("review_blocked")}</p>}</div>
                <FileText className="mt-1 h-4 w-4 text-text-4 transition-colors group-hover:text-accent-2"/>
              </button>
            </li>;
          })}
        </ol>
      )}
      {selected && <FlowCardDrawer projectName={projectName} card={asFlowCard(selected)} column={HUMAN_COLUMN} contentMode={contentMode} busy={busyEpisode === selected.episode} reviewVersion={reviewVersion} onApprove={() => void approve(selected)} onRedo={() => setRedoTarget(selected)} onClose={() => { setSelectedId(null); refresh(); }} onScreenplayLoaded={(screenplay: Screenplay) => { if (screenplay.fingerprint) reviewedScreenplay.current[screenplay.episode] = screenplay.fingerprint; }}/>} 
      <FlowRedoDialog key={redoTarget?.id ?? "none"} open={redoTarget != null} episode={redoTarget?.episode ?? 0} loading={redoTarget != null && busyEpisode === redoTarget.episode} onSubmit={submitRedo} onCancel={() => setRedoTarget(null)}/>
      {overwriteTarget && <ScriptOverwriteConfirmDialog open overwrite={overwriteTarget.overwrite} loading={busyEpisode === overwriteTarget.task.episode} onConfirm={async () => { if (await approve(overwriteTarget.task, overwriteTarget.overwrite.revision)) setOverwriteTarget(null); }} onCancel={() => setOverwriteTarget(null)}/>} 
    </div>
  );
}
