import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, GripVertical, ShieldAlert, UserRound } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import type { FlowBoard as FlowBoardData, FlowCard, FlowColumn } from "@/types/flow";
import type { ScriptOverwrite } from "@/types";
import type { Screenplay } from "@/types/screenplay";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { FlowCardDrawer } from "./FlowCardDrawer";
import { FlowRedoDialog } from "./FlowRedoDialog";
import { cardsByColumn, resolveDrop } from "./flow-rules";

/** 确认被拒是因为该集已有正式脚本、未认可覆盖时，取出服务端列出的覆盖后果。 */
function overwriteFromError(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const overwrite = (diagnostic as { script_overwrite?: unknown }).script_overwrite;
  return overwrite != null && typeof overwrite === "object" ? (overwrite as ScriptOverwrite) : null;
}

function BoardCard({
  card,
  draggable,
  selected,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  card: FlowCard;
  draggable: boolean;
  selected: boolean;
  dragging: boolean;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const title = card.title || t("flow_card_untitled");
  return (
    <li
      draggable={draggable}
      onDragStart={(e) => {
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(card.episode));
        }
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      data-testid={`flow-card-${card.episode}`}
      className={`rounded-[10px] border transition-colors ${
        selected ? "border-accent/55" : "border-hairline hover:border-hairline-strong"
      } ${dragging ? "opacity-40" : ""} ${draggable ? "cursor-grab active:cursor-grabbing" : ""}`}
      style={{ background: "linear-gradient(180deg, oklch(0.21 0.011 265 / 0.7), oklch(0.17 0.010 265 / 0.7))" }}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-pressed={selected}
        className="flex w-full items-start gap-2 rounded-[10px] px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {draggable && <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-4" aria-hidden="true" />}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-mono text-[11px] text-text-3">{t("flow_card_label", { episode: card.episode })}</span>
          <span className="truncate text-[13px] text-text">{title}</span>
        </span>
        {card.blocked && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded bg-amber-400/10 px-1.5 py-0.5 text-[10.5px] text-amber-400">
            <ShieldAlert className="h-3 w-3" aria-hidden="true" />
            {t("flow_card_blocked")}
          </span>
        )}
      </button>
    </li>
  );
}

/** Flow 看板：每集一张卡片，按阶段分列。人工列卡片拖到下一列 = 通过，拖回上一个 Agent 列 = 请 Agent 重做。 */
export function FlowBoard({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const contentMode = useProjectsStore((s) => s.currentProjectData?.content_mode);
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const settledTasks = useTasksStore((s) => s.stats.succeeded + s.stats.failed + s.stats.cancelled);

  const [board, setBoard] = useState<FlowBoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [reviewVersion, setReviewVersion] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [dragging, setDragging] = useState<FlowCard | null>(null);
  const [overTarget, setOverTarget] = useState<string | null>(null);
  const [busyEpisode, setBusyEpisode] = useState<number | null>(null);
  const [redoTarget, setRedoTarget] = useState<FlowCard | null>(null);
  const [overwriteTarget, setOverwriteTarget] = useState<{ card: FlowCard; overwrite: ScriptOverwrite } | null>(null);
  /** 审阅面板最近读到的剧本指纹（按集）：「通过」带上它，保证通过的是用户看过的那一版。 */
  const reviewedScreenplay = useRef<Record<number, string>>({});
  const onScreenplayLoaded = useCallback((screenplay: Screenplay) => {
    if (screenplay.fingerprint) reviewedScreenplay.current[screenplay.episode] = screenplay.fingerprint;
  }, []);

  const refresh = useCallback(() => {
    setReviewVersion((n) => n + 1);
    setRefreshTick((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    API.getFlowBoard(projectName, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setBoard(res);
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(errMsg(err));
        setLoading(false);
      });
    return () => controller.abort();
  }, [projectName, snapshotRevision, settledTasks, refreshTick]);

  const grouped = useMemo(() => (board ? cardsByColumn(board) : null), [board]);
  const columns = board?.columns;
  const selectedCard = board?.cards.find((c) => c.episode === selected) ?? null;
  const selectedColumn = selectedCard ? columns?.find((c) => c.id === selectedCard.column) : undefined;

  const reportFailure = useCallback(
    (err: unknown) => pushToast(t("flow_action_failed", { message: errMsg(err) }), "error"),
    [pushToast, t],
  );

  const approve = useCallback(
    async (card: FlowCard, overwriteRevision?: string): Promise<boolean> => {
      setBusyEpisode(card.episode);
      try {
        if (card.next_action === "review_screenplay") {
          const fingerprint =
            reviewedScreenplay.current[card.episode] ?? (await API.getScreenplay(projectName, card.episode)).fingerprint;
          if (!fingerprint) throw new Error(t("screenplay_none"));
          await API.approveScreenplay(projectName, card.episode, fingerprint);
        } else if (card.column === "script_plan_review") {
          await API.confirmScriptReview(projectName, card.episode, { overwriteRevision });
        } else {
          await API.decideDirectionReview(projectName, card.episode, { decision: "approve" });
        }
        pushToast(t("flow_approved_toast", { episode: card.episode }), "success");
        refresh();
        return true;
      } catch (err) {
        const overwrite = overwriteRevision === undefined ? overwriteFromError(err) : null;
        if (overwrite) setOverwriteTarget({ card, overwrite });
        else reportFailure(err);
        return false;
      } finally {
        setBusyEpisode(null);
      }
    },
    [projectName, pushToast, refresh, reportFailure, t],
  );

  const submitRedo = useCallback(
    async (note: string) => {
      const card = redoTarget;
      if (!card) return;
      setBusyEpisode(card.episode);
      try {
        await API.decideDirectionReview(projectName, card.episode, {
          decision: "redo",
          ...(note ? { note } : {}),
        });
        pushToast(t("flow_redo_done_toast", { episode: card.episode }), "success");
        setRedoTarget(null);
        refresh();
      } catch (err) {
        reportFailure(err);
      } finally {
        setBusyEpisode(null);
      }
    },
    [redoTarget, projectName, pushToast, refresh, reportFailure, t],
  );

  const handleDrop = (column: FlowColumn) => {
    const card = dragging;
    setDragging(null);
    setOverTarget(null);
    if (!card || !columns || busyEpisode !== null) return;
    const outcome = resolveDrop(columns, card, column.id);
    if (outcome.kind === "noop") return;
    if (outcome.kind === "approve") void approve(card);
    else if (outcome.kind === "redo") setRedoTarget(card);
    else pushToast(t(`flow_drop_${outcome.reason}`), "warning");
  };

  const closeDrawer = useCallback(() => {
    setSelected(null);
    refresh();
  }, [refresh]);

  if (loading && !board) {
    return <div className="flex h-full items-center justify-center text-text-4">{t("flow_loading")}</div>;
  }
  if (error && !board) {
    return (
      <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <p className="text-[13px] font-medium text-text-2">{t("flow_load_failed")}</p>
        <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{error}</p>
        <button type="button" onClick={refresh} className={GHOST_BTN_LG_CLS}>
          {t("flow_retry")}
        </button>
      </div>
    );
  }
  if (!board || !grouped) return null;
  if (board.cards.length === 0) {
    return <div className="flex h-full items-center justify-center px-6 text-center text-text-3">{t("flow_empty")}</div>;
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <header className="px-5 pb-2 pt-4">
        <h1 className="display-serif text-[18px] font-semibold tracking-tight text-text">{t("flow_title")}</h1>
        <p className="mt-1 max-w-3xl text-[12px] leading-relaxed text-text-3">{t("flow_hint")}</p>
        {error && (
          <p role="alert" className="mt-1 text-[11.5px] text-amber-400">
            {t("flow_load_failed")}: {error}
          </p>
        )}
      </header>

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto px-5 pb-5 pt-2">
        {board.columns.map((column) => {
          const cards = grouped.get(column.id) ?? [];
          const title = t(`flow_col_${column.id}`);
          const human = column.kind === "human";
          const droppable =
            dragging != null && ["approve", "redo"].includes(resolveDrop(board.columns, dragging, column.id).kind);
          return (
            <section
              key={column.id}
              aria-label={t("flow_column_aria", { title, count: cards.length })}
              data-testid={`flow-column-${column.id}`}
              data-kind={column.kind}
              onDragOver={(e) => {
                if (!dragging) return;
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = droppable ? "move" : "none";
                if (overTarget !== column.id) setOverTarget(column.id);
              }}
              onDragLeave={() => setOverTarget((cur) => (cur === column.id ? null : cur))}
              onDrop={(e) => {
                e.preventDefault();
                handleDrop(column);
              }}
              className={`flex w-[260px] shrink-0 flex-col rounded-[12px] border transition-colors ${
                human ? "border-accent/30 bg-accent-dim/30" : "border-hairline bg-bg-grad-a/30"
              } ${droppable && overTarget === column.id ? "ring-2 ring-accent" : ""}`}
            >
              <header className="flex items-center gap-2 border-b border-hairline px-3 py-2.5">
                <span className="grid h-5 w-5 shrink-0 place-items-center text-text-3" aria-hidden="true">
                  {human ? <UserRound className="h-3.5 w-3.5 text-accent-2" /> : <Bot className="h-3.5 w-3.5" />}
                </span>
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-[12.5px] font-medium text-text">{title}</h2>
                  <p className="text-[10.5px] text-text-4">{t(`flow_kind_${column.kind}`)}</p>
                </div>
                <span className="rounded-full bg-bg-grad-a px-2 py-0.5 font-mono text-[11px] text-text-3">
                  {cards.length}
                </span>
              </header>
              {cards.length === 0 ? (
                <p className="px-3 py-4 text-[11.5px] text-text-4">{t("flow_column_empty")}</p>
              ) : (
                <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2">
                  {cards.map((card) => (
                    <BoardCard
                      key={card.episode}
                      card={card}
                      draggable={human && busyEpisode === null}
                      selected={selected === card.episode}
                      dragging={dragging?.episode === card.episode}
                      onOpen={() => setSelected(card.episode)}
                      onDragStart={() => setDragging(card)}
                      onDragEnd={() => {
                        setDragging(null);
                        setOverTarget(null);
                      }}
                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      {selectedCard && selectedColumn && (
        <FlowCardDrawer
          projectName={projectName}
          card={selectedCard}
          column={selectedColumn}
          contentMode={contentMode}
          busy={busyEpisode === selectedCard.episode}
          reviewVersion={reviewVersion}
          onApprove={() => void approve(selectedCard).then((ok) => ok && setSelected(null))}
          onRedo={() => setRedoTarget(selectedCard)}
          onClose={closeDrawer}
          onScreenplayLoaded={onScreenplayLoaded}
        />
      )}

      <FlowRedoDialog
        key={redoTarget?.episode ?? "none"}
        open={redoTarget != null}
        episode={redoTarget?.episode ?? 0}
        loading={redoTarget != null && busyEpisode === redoTarget.episode}
        onSubmit={submitRedo}
        onCancel={() => setRedoTarget(null)}
      />

      {overwriteTarget && (
        <ScriptOverwriteConfirmDialog
          open
          overwrite={overwriteTarget.overwrite}
          loading={busyEpisode === overwriteTarget.card.episode}
          onConfirm={async () => {
            if (await approve(overwriteTarget.card, overwriteTarget.overwrite.revision)) setOverwriteTarget(null);
          }}
          onCancel={() => setOverwriteTarget(null)}
        />
      )}
    </div>
  );
}
