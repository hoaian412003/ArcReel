import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, CheckCheck, Copy, FileText, ImageOff, Loader2, RotateCcw, Sparkles, Undo2 } from "lucide-react";
import { API } from "@/api";
import { enqueueKeyframeSheet } from "@/actions/generation";
import { KeyframeSheetRequestDialog } from "@/components/canvas/reference/KeyframeSheetRequestDialog";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CARD_STYLE, GHOST_BTN_CLS, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, selectActiveResourceIds, useTasksStore } from "@/stores/tasks-store";
import { keyframeSheetResourceId } from "@/types/keyframe";
import type { KeyframeBoard, KeyframeBoardApplicable, KeyframeCell, KeyframeSheet } from "@/types/keyframe";
import { errMsg } from "@/utils/async";

const NO_CELL = "";
const APPROVE_ALL_KEY = "approve-all";
const cellKey = (cellId: string) => `cell:${cellId}`;
const unitKey = (unitId: string) => `unit:${unitId}`;

/** 单元正文里的 `@[名称]` 引用记号去掉括号，只留名字，便于在表格里阅读。 */
function plainMentions(text: string): string {
  return text.replace(/@\[([^\]]+)\]/g, "$1");
}

/** 联合图当前不能生成的原因对应的文案键；未登记的原因落到通用说明。 */
function problemKey(code: string): string {
  switch (code) {
    case "keyframe_scene_sheet_missing":
    case "keyframe_character_sheet_missing":
    case "keyframe_character_missing":
      return `keyframe_problem_${code.replace("keyframe_", "")}`;
    default:
      return "keyframe_problem_other";
  }
}

function StatusChip({ cell }: { cell: KeyframeCell }) {
  const { t } = useTranslation("dashboard");
  if (cell.image && cell.stale) {
    return (
      <span className="rounded border border-amber-400/40 bg-amber-400/10 px-1.5 py-px text-[10.5px] text-amber-300">
        {t("keyframe_cell_stale")}
      </span>
    );
  }
  const tone =
    cell.status === "approved"
      ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
      : "border-hairline bg-bg-grad-a/55 text-text-3";
  return (
    <span className={`rounded border px-1.5 py-px text-[10.5px] ${tone}`}>{t(`keyframe_cell_status_${cell.status}`)}</span>
  );
}

function CellCard({
  cell,
  projectName,
  cacheBust,
  busy,
  onReview,
}: {
  cell: KeyframeCell;
  projectName: string;
  cacheBust: number;
  busy: boolean;
  onReview: (cell: KeyframeCell, approved: boolean) => void;
}) {
  const { t } = useTranslation("dashboard");
  const approved = cell.status === "approved";
  const blocked = !cell.image ? "keyframe_approve_blocked_no_image" : cell.stale ? "keyframe_approve_blocked_stale" : null;

  return (
    <li
      className="flex flex-col gap-2 rounded-[10px] border border-hairline p-2.5"
      style={CARD_STYLE}
      data-testid={`keyframe-cell-${cell.id}`}
      data-status={cell.status}
    >
      <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-[6px] bg-black/40">
        {cell.image ? (
          <img
            src={API.getFileUrl(projectName, cell.image, cacheBust)}
            alt={cell.label}
            className="h-full w-full object-contain"
            loading="lazy"
          />
        ) : (
          <span className="flex flex-col items-center gap-1 text-[11.5px] text-text-4">
            <ImageOff className="h-4 w-4" aria-hidden="true" />
            {t("keyframe_cell_not_generated")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-text" title={cell.label}>
          {cell.label}
        </span>
        <span className="font-mono text-[10.5px] text-text-4">{cell.id}</span>
        <StatusChip cell={cell} />
      </div>
      <dl className="flex flex-col gap-1 text-[11.5px] leading-snug">
        <div>
          <dt className="inline text-text-4">{t("keyframe_cell_framing")}</dt>
          <dd className="ml-1.5 inline text-text-2">{cell.framing}</dd>
        </div>
        <div>
          <dt className="inline text-text-4">{t("keyframe_cell_staging")}</dt>
          <dd className="ml-1.5 inline text-text-2">{cell.staging}</dd>
        </div>
        <div>
          <dt className="inline text-text-4">{t("keyframe_cell_units")}</dt>
          <dd className="ml-1.5 inline font-mono text-text-3">{cell.unit_ids.join(" · ")}</dd>
        </div>
      </dl>
      {(blocked || !approved) && (
        <p className={`text-[11px] leading-snug ${blocked ? "text-amber-300/90" : "text-text-4"}`}>
          {blocked ? t(blocked) : t("keyframe_face_check_hint")}
        </p>
      )}
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          className={GHOST_BTN_CLS}
          disabled={busy || (!approved && blocked !== null)}
          onClick={() => onReview(cell, !approved)}
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : approved ? (
            <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {approved ? t("keyframe_unapprove") : t("keyframe_approve")}
        </button>
      </div>
    </li>
  );
}

function SheetCard({
  sheet,
  projectName,
  episode,
  cacheBust,
  generating,
  pending,
  onReview,
  onGenerate,
}: {
  sheet: KeyframeSheet;
  projectName: string;
  episode: number;
  cacheBust: number;
  generating: boolean;
  /** 正在写入的操作键（`cell:K01`、`approve-all`…）；每个按钮只看自己的那一项。 */
  pending: ReadonlySet<string>;
  onReview: (cell: KeyframeCell, approved: boolean) => void;
  onGenerate: (sheet: KeyframeSheet) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [requestOpen, setRequestOpen] = useState(false);
  const hasImages = sheet.cells.some((cell) => cell.image);
  return (
    <section
      className="flex flex-col gap-3 rounded-[12px] border border-hairline px-4 py-3.5"
      style={CARD_STYLE}
      data-testid={`keyframe-sheet-${sheet.id}`}
      aria-labelledby={`keyframe-sheet-title-${episode}-${sheet.id}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={`keyframe-sheet-title-${episode}-${sheet.id}`} className="text-[14px] font-medium text-text">
          {sheet.scene}
        </h3>
        <span className="rounded border border-hairline bg-bg-grad-a/55 px-1.5 py-px font-mono text-[10.5px] text-text-3">
          {sheet.id}
        </span>
        <span className="flex-1" />
        <button type="button" className={GHOST_BTN_LG_CLS} onClick={() => setRequestOpen(true)}>
          <FileText className="h-3.5 w-3.5" aria-hidden="true" />
          {t("keyframe_view_request")}
        </button>
        <button
          type="button"
          className={GHOST_BTN_LG_CLS}
          disabled={generating || sheet.problem !== null}
          onClick={() => onGenerate(sheet)}
        >
          {generating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {generating
            ? t("keyframe_generating")
            : hasImages
              ? t("keyframe_regenerate_sheet")
              : t("keyframe_generate_sheet")}
        </button>
      </header>
      {sheet.problem && (
        <p role="alert" className="text-[12px] text-amber-300" data-testid={`keyframe-problem-${sheet.id}`}>
          {t(problemKey(sheet.problem.code), { name: sheet.problem.name })}
        </p>
      )}
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label={t("keyframe_cells_aria", { sheet: sheet.id })}>
        {sheet.cells.map((cell) => (
          <CellCard
            key={cell.id}
            cell={cell}
            projectName={projectName}
            cacheBust={cacheBust}
            busy={pending.has(cellKey(cell.id)) || pending.has(APPROVE_ALL_KEY)}
            onReview={onReview}
          />
        ))}
      </ul>
      {requestOpen && (
        <KeyframeSheetRequestDialog
          projectName={projectName}
          episode={episode}
          sheetId={sheet.id}
          onClose={() => setRequestOpen(false)}
        />
      )}
    </section>
  );
}

function UnitAssignments({
  board,
  pending,
  onAssign,
}: {
  board: KeyframeBoardApplicable;
  pending: ReadonlySet<string>;
  onAssign: (unitId: string, cellId: string | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  const cells = board.sheets.flatMap((sheet) => sheet.cells);
  return (
    <section className="rounded-[12px] border border-hairline px-4 py-3.5" style={CARD_STYLE}>
      <h3 className="mb-2.5 text-[13px] font-medium text-text">{t("keyframe_units_title")}</h3>
      <ul className="flex flex-col divide-y divide-[var(--color-hairline-soft)]" data-testid="keyframe-units">
        {board.units.map((unit) => (
          <li key={unit.unit_id} className="flex items-center gap-3 py-2">
            <span className="w-16 shrink-0 font-mono text-[11.5px] text-text-3">{unit.unit_id}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-text-2" title={plainMentions(unit.text)}>
              {plainMentions(unit.text)}
            </span>
            <select
              aria-label={t("keyframe_unit_select_aria", { unit: unit.unit_id })}
              className="rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 text-[12px] text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
              disabled={pending.has(unitKey(unit.unit_id))}
              value={unit.cell_id ?? NO_CELL}
              onChange={(e) => onAssign(unit.unit_id, e.target.value === NO_CELL ? null : e.target.value)}
            >
              <option value={NO_CELL}>{t("keyframe_units_none")}</option>
              {cells.map((cell) => (
                <option key={cell.id} value={cell.id}>
                  {`${cell.id} · ${cell.label}`}
                </option>
              ))}
            </select>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * 起始帧看板（参考生视频、H3 提示词档）：联合图与格子的审阅、单元与格子的对应。
 *
 * 规划由 Agent 的 plan-keyframes 子智能体提交，这里只做其后人工能做的事：生成联合图、
 * 逐格核对（尤其人脸）并审核、把单元改挂到另一格。已审核的格子才会随视频请求发出。
 */
export function KeyframeBoardPanel({ projectName, episode }: { projectName: string; episode: number }) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const target = `${projectName}:${episode}`;
  const [result, setResult] = useState<{
    target: string;
    board: KeyframeBoard | null;
    error: string | null;
    loadedAt: number;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const [enqueueingAll, setEnqueueingAll] = useState(false);
  const [confirmGenerateAll, setConfirmGenerateAll] = useState(false);
  // 写操作在服务端是「读—改—存」整份看板，并发会互相覆盖：按点击顺序排队执行，各按钮仍各自显示自己的进行态。
  const writeQueue = useRef<Promise<void>>(Promise.resolve());

  const tasks = useTasksStore((s) => s.tasks);
  const optimisticActive = useTasksStore((s) => s.optimisticActive);
  const generatingIds = useMemo(
    () => selectActiveResourceIds(tasks, "keyframe_sheet", projectName, optimisticActive),
    [tasks, projectName, optimisticActive],
  );

  useEffect(() => {
    const controller = new AbortController();
    API.getKeyframeBoard(projectName, episode, { signal: controller.signal })
      .then((board) => {
        if (!controller.signal.aborted) setResult({ target, board, error: null, loadedAt: Date.now() });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setResult({ target, board: null, error: errMsg(err), loadedAt: Date.now() });
      });
    return () => controller.abort();
  }, [projectName, episode, target, attempt]);

  // 联合图任务从在跑变为结束时重读看板：图与格子状态在落格那一刻才写回。
  const wasGenerating = useRef(false);
  const isGenerating = generatingIds.size > 0;
  useEffect(() => {
    if (wasGenerating.current && !isGenerating) {
       
      setAttempt((n) => n + 1);
    }
    wasGenerating.current = isGenerating;
  }, [isGenerating]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const current = result?.target === target ? result : null;

  const write = useCallback(
    (key: string, action: () => Promise<KeyframeBoard>, failureKey: string) => {
      const mark = (add: boolean) =>
        setPending((prev) => {
          const next = new Set(prev);
          if (add) next.add(key);
          else next.delete(key);
          return next;
        });
      mark(true);
      writeQueue.current = writeQueue.current.then(async () => {
        try {
          const board = await action();
          setResult({ target, board, error: null, loadedAt: Date.now() });
        } catch (err) {
          pushToast(t(failureKey, { message: errMsg(err) }), "error");
          retry();
        } finally {
          mark(false);
        }
      });
    },
    [target, pushToast, retry, t],
  );

  const handleReview = useCallback(
    (cell: KeyframeCell, approved: boolean) => {
      write(cellKey(cell.id), () => API.reviewKeyframeCell(projectName, episode, cell.id, approved), "keyframe_review_failed");
    },
    [write, projectName, episode],
  );

  const handleAssign = useCallback(
    (unitId: string, cellId: string | null) => {
      write(unitKey(unitId), () => API.assignKeyframeUnit(projectName, episode, unitId, cellId), "keyframe_assign_failed");
    },
    [write, projectName, episode],
  );

  const handleApproveAll = useCallback(() => {
    write(APPROVE_ALL_KEY, () => API.approveAllKeyframeCells(projectName, episode), "keyframe_review_failed");
  }, [write, projectName, episode]);

  const handleGenerate = useCallback(
    async (sheet: KeyframeSheet) => {
      if (isResourceBusy("keyframe_sheet", projectName, keyframeSheetResourceId(episode, sheet.id))) return;
      try {
        await enqueueKeyframeSheet(projectName, episode, sheet.id);
      } catch (err) {
        pushToast(t("keyframe_generate_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, episode, pushToast, t],
  );

  const handleGenerateAll = useCallback(
    async (sheets: KeyframeSheet[]) => {
      setConfirmGenerateAll(false);
      setEnqueueingAll(true);
      try {
        for (const sheet of sheets) {
          if (sheet.problem !== null) continue;
          if (isResourceBusy("keyframe_sheet", projectName, keyframeSheetResourceId(episode, sheet.id))) continue;
          try {
            await enqueueKeyframeSheet(projectName, episode, sheet.id);
          } catch (err) {
            pushToast(t("keyframe_generate_failed", { message: errMsg(err) }), "error");
          }
        }
      } finally {
        setEnqueueingAll(false);
      }
    },
    [projectName, episode, pushToast, t],
  );

  const agentPrompt = t("keyframe_agent_prompt", { episode });
  const copyPrompt = useCallback(() => {
    void navigator.clipboard
      ?.writeText(agentPrompt)
      .then(() => pushToast(t("keyframe_prompt_copied"), "success"))
      .catch(() => pushToast(t("keyframe_prompt_copy_failed"), "error"));
  }, [agentPrompt, pushToast, t]);

  if (current == null) {
    return <div className="flex h-40 items-center justify-center text-text-4">{t("keyframe_loading")}</div>;
  }
  if (current.error || current.board == null) {
    return (
      <div role="alert" className="flex h-40 flex-col items-center justify-center gap-3 text-center">
        <p className="text-[13px] font-medium text-text-2">{t("keyframe_failed")}</p>
        <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{current.error}</p>
        <button type="button" onClick={retry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          {t("flow_retry")}
        </button>
      </div>
    );
  }
  const board = current.board;
  if (!board.applicable) {
    return <p className="text-[12.5px] text-text-3">{t("keyframe_not_applicable")}</p>;
  }
  if (!board.has_board) {
    return (
      <div className="flex flex-col gap-3 rounded-[12px] border border-hairline px-4 py-4" style={CARD_STYLE}>
        <h3 className="text-[14px] font-medium text-text">{t("keyframe_empty_title")}</h3>
        <p className="max-w-prose text-[12.5px] leading-relaxed text-text-2">{t("keyframe_empty_body")}</p>
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 font-mono text-[12px] text-text-2">
            {agentPrompt}
          </code>
          <button type="button" className={GHOST_BTN_CLS} onClick={copyPrompt}>
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            {t("keyframe_copy_prompt")}
          </button>
        </div>
      </div>
    );
  }

  const generatableSheets = board.sheets.filter(
    (sheet) =>
      sheet.problem === null &&
      !generatingIds.has(keyframeSheetResourceId(episode, sheet.id)),
  );
  const anyImages = generatableSheets.some((sheet) => sheet.cells.some((cell) => cell.image));
  const approvableCount = board.sheets
    .flatMap((sheet) => sheet.cells)
    .filter((cell) => cell.image && !cell.stale && cell.status !== "approved").length;

  return (
    <div className="flex flex-col gap-4 pb-20" data-testid="keyframe-board">
      {board.plan_stale && (
        <p role="status" className="rounded-[8px] border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-[12px] text-amber-200">
          {t("keyframe_plan_stale")}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2" data-testid="keyframe-toolbar">
        <span className="flex-1" />
        <button
          type="button"
          className={GHOST_BTN_LG_CLS}
          disabled={enqueueingAll || !generatableSheets.length}
          onClick={() => (anyImages ? setConfirmGenerateAll(true) : void handleGenerateAll(board.sheets))}
        >
          {enqueueingAll ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {t("keyframe_generate_all", { count: generatableSheets.length })}
        </button>
        <button
          type="button"
          className={GHOST_BTN_LG_CLS}
          disabled={pending.has(APPROVE_ALL_KEY) || approvableCount === 0}
          onClick={handleApproveAll}
        >
          {pending.has(APPROVE_ALL_KEY) ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <CheckCheck className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {t("keyframe_approve_all", { count: approvableCount })}
        </button>
      </div>
      <ConfirmDialog
        open={confirmGenerateAll}
        tone="danger"
        title={t("keyframe_generate_all_confirm_title")}
        description={t("keyframe_generate_all_confirm_body", { count: generatableSheets.length })}
        confirmLabel={t("keyframe_generate_all", { count: generatableSheets.length })}
        onConfirm={() => void handleGenerateAll(board.sheets)}
        onCancel={() => setConfirmGenerateAll(false)}
      />
      {board.sheets.map((sheet) => (
        <SheetCard
          key={sheet.id}
          sheet={sheet}
          projectName={projectName}
          episode={episode}
          cacheBust={current.loadedAt}
          generating={generatingIds.has(keyframeSheetResourceId(episode, sheet.id))}
          pending={pending}
          onReview={handleReview}
          onGenerate={(item) => void handleGenerate(item)}
        />
      ))}
      <UnitAssignments board={board} pending={pending} onAssign={handleAssign} />
    </div>
  );
}
