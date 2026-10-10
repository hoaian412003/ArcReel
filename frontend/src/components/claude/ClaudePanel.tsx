import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  CircleStop,
  LoaderCircle,
  Plus,
  Send,
  ShieldAlert,
  Trash2,
  Users,
  Wrench,
  X,
  Brain,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ClaudeAPI, type ClaudeEvent, type ClaudeSession, type ClaudeUsageTotals } from "@/api/claude";
import { StreamMarkdown } from "@/components/shared/StreamMarkdown";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { useClaudeStore } from "@/stores/claude-store";
import { useCanvasContextStore } from "@/stores/canvas-context-store";
import { ClaudeAgentSettings } from "./ClaudeAgentSettings";
import {
  activitySummary,
  formatActivityDetail,
  projectClaudeTimeline,
  type ClaudeActivityItem,
} from "./claudeTimeline";

interface ClaudePanelProps {
  projectName: string;
  onClose: () => void;
}

function messageRecord(event: ClaudeEvent): Record<string, unknown> | null {
  const value = event.payload.message;
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function assistantText(event: ClaudeEvent): string | null {
  if (event.type !== "sdk_message") return null;
  const message = messageRecord(event);
  if (message?.message_type !== "AssistantMessage" || !Array.isArray(message.content)) return null;
  const text = message.content
    .map((block) => block && typeof block === "object" && typeof (block as { text?: unknown }).text === "string"
      ? (block as { text: string }).text
      : "")
    .join("");
  return text || null;
}

function deltaText(event: ClaudeEvent): string {
  if (event.type !== "message_delta") return "";
  const message = messageRecord(event);
  const sdkEvent = message?.event;
  if (!sdkEvent || typeof sdkEvent !== "object") return "";
  const delta = (sdkEvent as { delta?: unknown }).delta;
  return delta && typeof delta === "object" && typeof (delta as { text?: unknown }).text === "string"
    ? (delta as { text: string }).text
    : "";
}

function ResponseSignal({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2.5 px-1 py-1 text-[11px] text-white/55">
      <span className="claude-response-signal" aria-hidden>
        <span />
        <span />
        <span />
      </span>
      <span>{label}</span>
    </div>
  );
}

function ActivityCard({
  activity,
  labels,
}: {
  activity: ClaudeActivityItem;
  labels: {
    running: string;
    completed: string;
    error: string;
    stopped: string;
    subagent: string;
    details: string;
    input: string;
    output: string;
    currentTool: string;
  };
}) {
  const statusLabel = labels[activity.status];
  const summary = activitySummary(activity);
  const input = formatActivityDetail(activity.input);
  const result = formatActivityDetail(activity.result);
  const isRunning = activity.status === "running";
  const isError = activity.status === "error";
  const Icon = activity.activityKind === "subagent" ? Users : Wrench;
  const StatusIcon = isRunning
    ? LoaderCircle
    : isError
      ? AlertCircle
      : activity.status === "stopped"
        ? CircleStop
        : Check;

  return (
    <div
      className={`rounded-lg border px-2.5 py-2 text-xs ${activity.parentToolUseId ? "ml-5" : ""} ${
        isError ? "border-red-400/25 bg-red-400/[0.06]" : "border-white/10 bg-white/[0.025]"
      }`}
      data-testid={activity.activityKind === "subagent" ? "claude-subagent" : "claude-tool-call"}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon className="h-3.5 w-3.5 shrink-0 text-white/55" aria-hidden />
        <span className="min-w-0 truncate font-medium text-white/85">
          {activity.activityKind === "subagent" ? `${labels.subagent} · ` : ""}{activity.name}
        </span>
        <span
          role="status"
          aria-live={isRunning ? "polite" : "off"}
          className={`ml-auto flex shrink-0 items-center gap-1 text-[10px] ${
            isError ? "text-red-300" : isRunning ? "text-[#d7ff45]/80" : "text-white/45"
          }`}
        >
          <StatusIcon className={`h-3 w-3 ${isRunning ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden />
          {statusLabel}
        </span>
      </div>
      {summary ? <p className="mt-1 truncate pl-[22px] text-[11px] text-white/50" title={summary}>{summary}</p> : null}
      {activity.lastToolName ? (
        <p className="mt-1 pl-[22px] text-[10px] text-white/40">
          {labels.currentTool}: {activity.lastToolName}
        </p>
      ) : null}
      {input || result ? (
        <details className="group mt-1.5 pl-[22px]">
          <summary className="cursor-pointer select-none text-[10px] text-white/40 hover:text-white/65">{labels.details}</summary>
          <div className="mt-1.5 space-y-1.5">
            {input ? (
              <div>
                <div className="mb-0.5 text-[9px] uppercase tracking-wider text-white/35">{labels.input}</div>
                <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-black/20 p-2 text-[10px] text-white/55">{input}</pre>
              </div>
            ) : null}
            {result ? (
              <div>
                <div className="mb-0.5 text-[9px] uppercase tracking-wider text-white/35">{labels.output}</div>
                <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-black/20 p-2 text-[10px] text-white/55">{result}</pre>
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

const EMPTY_IDS: readonly string[] = [];
/** 首屏只渲染最近 N 条时间线，向上滚动时每次多展开 N 条。 */
const WINDOW_SIZE = 40;
const STICK_THRESHOLD_PX = 48;
const LOAD_MORE_THRESHOLD_PX = 160;
const EARLIER_TURNS = 10;

export function ClaudePanel({ projectName, onClose }: ClaudePanelProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((state) => state.pushToast);
  const [sessions, setSessions] = useState<ClaudeSession[]>(() => {
    const accepted = useClaudeStore.getState().accepted;
    return accepted?.projectName === projectName ? [accepted.session] : [];
  });
  const [sessionId, setSessionId] = useState<string | null>(() => useClaudeStore.getState().selected[projectName] || null);
  const [events, setEvents] = useState<ClaudeEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [input, setInput] = useState("");
  const contextIds = useCanvasContextStore((state) => state.ids[projectName] ?? EMPTY_IDS);
  const contextProvider = useCanvasContextStore((state) => state.providers[projectName]);
  const mentionOptions = useMemo(() => {
    const match = /@([^\s@]*)$/.exec(input);
    if (!match || !contextProvider) return [];
    const query = (match[1] ?? "").toLowerCase();
    return contextProvider.nodeIds().filter((id) => id.toLowerCase().includes(query)).slice(0, 8);
  }, [input, contextProvider]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const activeMention = Math.min(mentionIndex, Math.max(mentionOptions.length - 1, 0));
  const [sending, setSending] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [queued, setQueued] = useState(false);
  const [usage, setUsage] = useState<ClaudeUsageTotals | null>(null);
  const [revertedSnapshots, setRevertedSnapshots] = useState<Set<string>>(() => new Set());
  const [resolvedPermissions, setResolvedPermissions] = useState<Set<string>>(() => new Set());
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const pendingEventsRef = useRef<ClaudeEvent[]>([]);
  const seenSeqRef = useRef(new Set<number>());
  const flushFrameRef = useRef<number | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const loadingEarlierRef = useRef(false);
  const restoreScrollRef = useRef<{ height: number; top: number } | null>(null);
  const [visibleCount, setVisibleCount] = useState(WINDOW_SIZE);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const chooseSession = useCallback((nextSessionId: string | null) => {
    setEvents([]);
    seenSeqRef.current = new Set();
    loadingEarlierRef.current = false;
    pendingEventsRef.current = [];
    setVisibleCount(WINDOW_SIZE);
    stickToBottomRef.current = true;
    setDraft("");
    setUsage(null);
    setSending(false);
    setQueued(false);
    setResolvedPermissions(new Set());
    setSessionId(nextSessionId);
    useClaudeStore.getState().select(projectName, nextSessionId);
  }, [projectName]);

  useEffect(() => {
    let cancelled = false;
    void ClaudeAPI.listSessions(projectName)
      .then((items) => {
        if (cancelled) return;
        setSessions(items);
        const preferred = useClaudeStore.getState().selected[projectName];
        // 列表按最近更新倒序，首项即最新会话。
        chooseSession(preferred || items[0]?.id || null);
      })
      .catch((error) => pushToast(errMsg(error), "error"));
    return () => { cancelled = true; };
  }, [chooseSession, projectName, pushToast]);

  useEffect(() => useClaudeStore.subscribe((next, previous) => {
    const accepted = next.accepted;
    if (accepted === previous.accepted || !accepted || accepted.projectName !== projectName) return;
    setSessions((current) => [accepted.session, ...current.filter((item) => item.id !== accepted.session.id)]);
    if (accepted.session.id !== sessionId) chooseSession(accepted.session.id);
    setSending(true);
    setQueued(true);
    setShowSettings(false);
  }), [chooseSession, projectName, sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    const stream = ClaudeAPI.openEventStream({
      projectName,
      sessionId,
      onEvent(event) {
        if (!seenSeqRef.current.has(event.seq)) {
          seenSeqRef.current.add(event.seq);
          pendingEventsRef.current.push(event);
          flushFrameRef.current ??= requestAnimationFrame(() => {
            flushFrameRef.current = null;
            const batch = pendingEventsRef.current;
            pendingEventsRef.current = [];
            if (batch.length) setEvents((current) => [...current, ...batch]);
          });
        }
        const token = deltaText(event);
        if (token) setDraft((current) => current + token);
        if (assistantText(event)) setDraft("");
        if (event.type === "permission_result" && typeof event.payload.request_id === "string") {
          setResolvedPermissions((current) => new Set(current).add(event.payload.request_id as string));
        }
        const message = messageRecord(event);
        if (event.type === "turn_queued") { setSending(true); setQueued(true); }
        if (event.type === "turn_running") { setSending(true); setQueued(false); }
        if (event.type === "turn_error" && event.payload.code !== "interrupted") {
          pushToast(t("claude_turn_failed", { code: typeof event.payload.code === "string" ? event.payload.code : "" }), "error");
        }
        if (
          event.type === "turn_error"
          || event.type === "turn_finished"
          || (event.type === "sdk_message" && message?.message_type === "ResultMessage")
        ) {
          setSending(false);
          void ClaudeAPI.getUsage(projectName, sessionId).then((data) => {
            setUsage(data.totals);
            setSending(data.turns.some((turn) => turn.status === "accepted" || turn.status === "running"));
            setQueued(data.turns.some((turn) => turn.status === "accepted"));
          }).catch((error) => pushToast(errMsg(error), "error"));
        }
      },
      onError(error) {
        if (!error.retryable) pushToast(error.message, "error");
      },
    });
    void ClaudeAPI.getUsage(projectName, sessionId).then((data) => {
      setUsage(data.totals);
      if (data.turns.length) {
        setSending(data.turns.some((turn) => turn.status === "accepted" || turn.status === "running"));
        setQueued(data.turns.some((turn) => turn.status === "accepted"));
      }
    }).catch((error) => pushToast(errMsg(error), "error"));
    return () => {
      stream.close();
      if (flushFrameRef.current !== null) cancelAnimationFrame(flushFrameRef.current);
      flushFrameRef.current = null;
      pendingEventsRef.current = [];
    };
  }, [projectName, pushToast, sessionId, t]);

  const timeline = useMemo(
    () => projectClaudeTimeline(events, resolvedPermissions),
    [events, resolvedPermissions],
  );
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);
  const hiddenCount = Math.max(timeline.length - visibleCount, 0);
  const visibleTimeline = hiddenCount > 0 ? timeline.slice(hiddenCount) : timeline;

  // 在浏览器绘制前完成滚动，避免先渲染在顶部再跳到底部。
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const restore = restoreScrollRef.current;
    if (restore) {
      restoreScrollRef.current = null;
      el.scrollTop = el.scrollHeight - restore.height + restore.top;
    } else if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [draft, events, visibleCount, showSettings]);

  const oldestSeq = events[0]?.seq ?? 1;

  const loadEarlier = (el: HTMLDivElement) => {
    if (!sessionId || loadingEarlierRef.current) return;
    loadingEarlierRef.current = true;
    const requestedSession = sessionId;
    void ClaudeAPI.getEarlierEvents(projectName, requestedSession, oldestSeq, EARLIER_TURNS)
      .then(({ events: older }) => {
        if (requestedSession !== sessionIdRef.current) return;
        const fresh = older.filter((event) => !seenSeqRef.current.has(event.seq));
        for (const event of fresh) seenSeqRef.current.add(event.seq);
        if (!fresh.length) return;
        restoreScrollRef.current = { height: el.scrollHeight, top: el.scrollTop };
        setEvents((current) => [...fresh, ...current]);
        setVisibleCount((count) => count + WINDOW_SIZE);
      })
      .catch((error) => pushToast(errMsg(error), "error"))
      .finally(() => { loadingEarlierRef.current = false; });
  };

  const handleScroll = (el: HTMLDivElement) => {
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
    if (el.scrollTop >= LOAD_MORE_THRESHOLD_PX || restoreScrollRef.current) return;
    if (hiddenCount > 0) {
      restoreScrollRef.current = { height: el.scrollHeight, top: el.scrollTop };
      setVisibleCount((count) => count + WINDOW_SIZE);
    } else if (oldestSeq > 1) {
      loadEarlier(el);
    }
  };
  const hasPendingPermission = useMemo(
    () => events.some((event) => (
      event.type === "permission_request"
      && typeof event.payload.request_id === "string"
      && !resolvedPermissions.has(event.payload.request_id)
    )),
    [events, resolvedPermissions],
  );

  const pickMention = (id: string) => {
    setInput((current) => current.replace(/@[^\s@]*$/, `@${id} `));
    useCanvasContextStore.getState().add(projectName, id);
    inputRef.current?.focus();
  };

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const content = contextProvider ? contextProvider.compose(text, contextIds) : text;
    const sentContextIds = [...contextIds];
    setInput("");
    useCanvasContextStore.getState().clear(projectName);
    setSending(true);
    try {
      const result = await ClaudeAPI.sendMessage(projectName, content, sessionId, crypto.randomUUID());
      setSessions((current) => {
        const rest = current.filter((item) => item.id !== result.session.id);
        return [result.session, ...rest];
      });
      if (result.session.id !== sessionId) {
        chooseSession(result.session.id);
        // Switching from the "new session" option clears transient UI state;
        // this accepted turn is still running and must keep its activity state.
        setSending(true);
      }
    } catch (error) {
      setSending(false);
      setInput((current) => current || text);
      for (const id of sentContextIds) useCanvasContextStore.getState().add(projectName, id);
      pushToast(errMsg(error), "error");
    }
  };

  const decidePermission = async (event: ClaudeEvent, allow: boolean) => {
    const requestId = event.payload.request_id;
    if (!sessionId || typeof requestId !== "string") return;
    try {
      await ClaudeAPI.resolvePermission(projectName, sessionId, requestId, allow);
      setResolvedPermissions((current) => new Set(current).add(requestId));
    } catch (error) {
      pushToast(errMsg(error), "error");
    }
  };

  const revertChanges = async (snapshotId: string) => {
    try {
      await ClaudeAPI.restoreSnapshot(projectName, snapshotId);
      setRevertedSnapshots((current) => new Set(current).add(snapshotId));
      pushToast(t("claude_changes_revert_done"), "success");
    } catch (error) {
      pushToast(errMsg(error), "error");
    }
  };

  const clearSessions = async () => {
    setClearing(true);
    try {
      const result = await ClaudeAPI.clearSessions(projectName);
      useClaudeStore.getState().clearProject(projectName);
      setSessions([]);
      chooseSession(null);
      setShowClearConfirm(false);
      pushToast(t("claude_sessions_cleared", { count: result.deleted, project: projectName }), "success");
    } catch (error) {
      pushToast(errMsg(error), "error");
    } finally {
      setClearing(false);
    }
  };

  return (
    <>
    <aside
      role="dialog"
      aria-modal="false"
      aria-label={t("claude_title")}
      className="claude-floating-window fixed bottom-3 right-3 z-40 flex h-[min(720px,calc(100dvh-5rem))] w-[min(520px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-[14px] bg-[#101116] shadow-[0_28px_80px_-24px_rgba(0,0,0,0.9)] sm:bottom-5 sm:right-5"
    >
      <div className="flex h-12 items-center gap-2 border-b border-white/10 px-3">
        <Bot className="h-4 w-4 text-[#d7ff45]" aria-hidden />
        <strong className="text-sm">{t("claude_title")}</strong>
        <button type="button" onClick={() => setShowSettings((current) => !current)} aria-label={t("claude_memory")} aria-pressed={showSettings} className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10"><Brain className="h-4 w-4" /></button>
        <div className="relative ml-auto">
          <select
            aria-label={t("claude_session")}
            value={sessionId ?? ""}
            onChange={(event) => chooseSession(event.target.value || null)}
            className="max-w-44 appearance-none rounded-md border border-white/10 bg-white/5 py-1 pl-2 pr-7 text-xs"
          >
            <option value="">{t("claude_new_session")}</option>
            <optgroup label={t("claude_project_sessions", { project: projectName })}>
              {sessions.map((session) => <option key={session.id} value={session.id}>{session.title || t("claude_session")}</option>)}
            </optgroup>
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1.5 h-3 w-3" aria-hidden />
        </div>
        <button type="button" onClick={() => chooseSession(null)} aria-label={t("claude_new_session")} className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#d7ff45]/60"><Plus className="h-4 w-4" /></button>
        <button type="button" disabled={sessions.length === 0 || sending} onClick={() => setShowClearConfirm(true)} aria-label={t("claude_clear_sessions")} className="grid h-8 w-8 place-items-center rounded-md text-white/55 hover:bg-red-400/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300/60"><Trash2 className="h-4 w-4" /></button>
        <button type="button" onClick={onClose} aria-label={t("claude_close")} className="grid h-8 w-8 place-items-center rounded-md hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#d7ff45]/60"><X className="h-4 w-4" /></button>
      </div>

      {showSettings ? <ClaudeAgentSettings projectName={projectName} /> : <div
        ref={scrollRef}
        onScroll={(event) => handleScroll(event.currentTarget)}
        className="flex-1 space-y-3 overflow-y-auto overscroll-contain p-3 [overflow-anchor:none]"
      >
        {timeline.length === 0 && !draft && !sending ? (
          <div className="mt-16 text-center text-sm text-white/45">{t("claude_empty")}</div>
        ) : null}
        {visibleTimeline.map((item) => {
          if (item.kind === "user") return <div key={item.key} className="ml-10 rounded-xl bg-[#d7ff45] px-3 py-2 text-sm text-black">{item.text}</div>;
          if (item.kind === "assistant") return <div key={item.key} className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2"><StreamMarkdown content={item.text} /></div>;
          if (item.kind === "activity") return (
            <ActivityCard
              key={item.key}
              activity={item}
              labels={{
                running: t("claude_activity_running"),
                completed: t("claude_activity_completed"),
                error: t("claude_activity_error"),
                stopped: t("claude_activity_stopped"),
                subagent: t("claude_subagent"),
                details: t("claude_activity_details"),
                input: t("claude_activity_input"),
                output: t("claude_activity_output"),
                currentTool: t("claude_subagent_current_tool"),
              }}
            />
          );
          if (item.kind === "changes") {
            const { changes } = item;
            const reverted = revertedSnapshots.has(changes.snapshotId);
            return (
              <div key={item.key} className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs">
                <div className="mb-1 font-medium text-white/80">
                  {t("claude_changes_title", { count: changes.total })}
                </div>
                <ul className="mb-2 max-h-28 overflow-auto text-[11px] text-white/55">
                  {[
                    ...changes.modified.map((file) => `~ ${file}`),
                    ...changes.added.map((file) => `+ ${file}`),
                    ...changes.removed.map((file) => `- ${file}`),
                  ].map((line) => <li key={line} className="truncate">{line}</li>)}
                </ul>
                {!changes.valid ? (
                  <div className="mb-2 text-[11px] text-amber-300">{t("claude_changes_invalid", { count: changes.errors })}</div>
                ) : null}
                <button
                  type="button"
                  disabled={reverted || sending}
                  onClick={() => void revertChanges(changes.snapshotId)}
                  className="rounded border border-white/15 px-2 py-1 disabled:opacity-40"
                >
                  {reverted ? t("claude_changes_reverted") : t("claude_changes_revert")}
                </button>
              </div>
            );
          }
          const event = item.event;
          return (
            <div key={item.key} className="rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-xs">
              <div className="mb-2 flex items-center gap-2 font-medium"><ShieldAlert className="h-4 w-4" />{t("claude_permission", { tool: typeof event.payload.tool_name === "string" ? event.payload.tool_name : "tool" })}</div>
              <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] text-white/60">{JSON.stringify(event.payload.tool_input, null, 2)}</pre>
              <div className="flex gap-2">
                <button type="button" onClick={() => void decidePermission(event, false)} className="rounded border border-white/15 px-2 py-1">{t("claude_deny")}</button>
                <button type="button" onClick={() => void decidePermission(event, true)} className="rounded bg-[#d7ff45] px-2 py-1 text-black">{t("claude_allow")}</button>
              </div>
            </div>
          );
        })}
        {draft ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2">
            <StreamMarkdown content={draft} />
            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-[#d7ff45]/65" aria-hidden>
              <span className="claude-stream-caret" />
              {t("claude_responding")}
            </div>
          </div>
        ) : null}
        {sending && !draft && !hasPendingPermission ? <ResponseSignal label={t(queued ? "claude_queued" : "claude_responding")} /> : null}
      </div>}

      {usage ? (
        <div className="border-t border-white/10 px-3 py-1.5 text-[10px] text-white/45">
          {t("claude_usage", { input: usage.input_tokens, output: usage.output_tokens, cost: usage.total_cost_usd.toFixed(4) })}
        </div>
      ) : null}
      <div className="border-t border-white/10 p-3">
        {contextIds.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {contextIds.map((id) => (
              <span key={id} className="inline-flex max-w-full items-center gap-1 rounded-full border border-[#d7ff45]/40 bg-[#d7ff45]/10 px-2 py-0.5 text-[11px] text-white/80">
                <span className="truncate">@{id}</span>
                <button type="button" aria-label={t("claude_context_remove")} onClick={() => useCanvasContextStore.getState().remove(projectName, id)} className="text-white/50 hover:text-white">×</button>
              </span>
            ))}
          </div>
        ) : null}
        {mentionOptions.length > 0 ? (
          <ul className="mb-2 max-h-40 overflow-auto rounded-lg border border-white/10 bg-black/40 text-xs" role="listbox">
            {mentionOptions.map((id, index) => (
              <li key={id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === activeMention}
                  className={`block w-full truncate px-2 py-1 text-left text-white/75 hover:bg-white/10 ${index === activeMention ? "bg-white/10" : ""}`}
                  onClick={() => pickMention(id)}
                >
                  @{id}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(event) => { setInput(event.target.value); setMentionIndex(0); }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            if (mentionOptions.length > 0) {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const step = event.key === "ArrowDown" ? 1 : -1;
                setMentionIndex((activeMention + step + mentionOptions.length) % mentionOptions.length);
                return;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                pickMention(mentionOptions[activeMention]);
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={t("claude_placeholder")}
          rows={3}
          className="w-full resize-none rounded-lg border border-white/10 bg-white/[0.04] p-2 text-sm outline-none focus:border-[#d7ff45]/50"
        />
        <div className="mt-2 flex justify-end gap-2">
          {sending && sessionId ? <button type="button" onClick={() => void ClaudeAPI.interrupt(projectName, sessionId)} className="rounded-md border border-white/15 p-2" aria-label={t("claude_stop")}><CircleStop className="h-4 w-4" /></button> : null}
          <button type="button" disabled={!input.trim() || sending} onClick={() => void send()} className="rounded-md bg-[#d7ff45] p-2 text-black disabled:opacity-40" aria-label={t("claude_send")}><Send className="h-4 w-4" /></button>
        </div>
      </div>
    </aside>
    <ConfirmDialog
      open={showClearConfirm}
      title={t("claude_clear_sessions_title")}
      description={t("claude_clear_sessions_hint", { project: projectName })}
      confirmLabel={t("claude_clear_sessions_confirm")}
      loadingLabel={t("claude_clearing_sessions")}
      tone="danger"
      loading={clearing}
      onConfirm={clearSessions}
      onCancel={() => setShowClearConfirm(false)}
    />
    </>
  );
}
