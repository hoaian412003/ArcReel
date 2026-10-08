import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { AlertTriangle, Clapperboard, Film, Image as ImageIcon, Layers, Loader2, PenLine, Play, Sparkles, Type, X } from "lucide-react";
import { API } from "@/api";
import type { CanvasAnyNodeType, CanvasHandleType, CanvasNodeData } from "@/types/canvas";
import { ACCENT_BTN_SM_CLS, ICON_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useCanvasActions } from "./canvas-context";
import { ASPECT_RATIOS, PASSIVE_INPUT_HANDLE, isBusy } from "./canvas-rules";

export type CanvasFlowNode = Node<CanvasNodeData, CanvasAnyNodeType>;

const HANDLE_COLOR: Record<CanvasHandleType, string> = {
  text: "var(--color-accent)",
  image: "oklch(0.74 0.13 190)",
  unit: "oklch(0.78 0.13 80)",
  h3: "oklch(0.72 0.14 150)",
};
const TEXT_SAVE_DELAY_MS = 600;

function handleStyle(type: CanvasHandleType, connectable = true) {
  return {
    width: 12,
    height: 12,
    background: HANDLE_COLOR[type],
    border: "2px solid oklch(0.16 0.010 265)",
    ...(connectable ? {} : { opacity: 0.35 }),
  };
}

function NodeShell({
  selected,
  width,
  icon,
  title,
  onRemove,
  children,
}: {
  selected: boolean;
  width: number;
  icon: ReactNode;
  title: string;
  onRemove?: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      style={{ width }}
      className={`rounded-[12px] border bg-bg-grad-a/90 shadow-lg backdrop-blur ${
        selected ? "border-accent/70" : "border-hairline"
      }`}
    >
      <div className="flex items-center gap-1.5 border-b border-hairline px-3 py-1.5 text-[11px] font-medium text-text-3">
        {icon}
        <span className="flex-1 truncate">{title}</span>
        {onRemove && (
          <button type="button" className={`nodrag ${ICON_BTN_CLS}`} onClick={onRemove} aria-label={t("node_canvas_delete")}>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

function HandleLabel({ side, children }: { side: "left" | "right"; children: ReactNode }) {
  return (
    <span className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-[10px] text-text-4 ${side === "left" ? "left-4" : "right-4"}`}>
      {children}
    </span>
  );
}

function TextNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setText, remove } = useCanvasActions();
  const [draft, setDraft] = useState(data.text ?? "");
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // 服务端回写的文本只在本地没有未保存改动时才覆盖草稿。
    if (!dirty.current) setDraft(data.text ?? "");
  }, [data.text]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const flush = (value: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    dirty.current = false;
    void setText(id, value);
  };

  return (
    <NodeShell
      selected={selected}
      width={280}
      icon={<Type className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.generated ? t("node_canvas_text_generated") : (data.label ?? t("node_canvas_text_label"))}
      onRemove={() => void remove(id)}
    >
      <textarea
        className={`nodrag nowheel ${INPUT_CLS} min-h-[96px] resize-y`}
        value={draft}
        placeholder={t("node_canvas_text_placeholder")}
        onChange={(e) => {
          dirty.current = true;
          setDraft(e.target.value);
          if (timer.current) clearTimeout(timer.current);
          const value = e.target.value;
          timer.current = setTimeout(() => flush(value), TEXT_SAVE_DELAY_MS);
        }}
        onBlur={() => {
          if (dirty.current) flush(draft);
        }}
      />
      {data.generated ? (
        <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("text", false)} />
      ) : null}
      <Handle type="source" position={Position.Right} id="text" style={handleStyle("text")} />
    </NodeShell>
  );
}

function ImageNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { projectName, remove } = useCanvasActions();
  const busy = isBusy(data.status);
  const failed = data.status === "failed";
  return (
    <NodeShell
      selected={selected}
      width={240}
      icon={<ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.label ?? t("node_canvas_image_label")}
      onRemove={busy ? undefined : () => void remove(id)}
    >
      {data.stale ? (
        <p className="mb-2 flex items-center gap-1.5 rounded-[6px] bg-amber-400/10 px-2 py-1 text-[10.5px] text-amber-300">
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          {t("node_canvas_stale")}
        </p>
      ) : null}
      <div className="flex min-h-[160px] items-center justify-center overflow-hidden rounded-[8px] border border-hairline bg-black/30">
        {data.path ? (
          <img
            src={API.getFileUrl(projectName, data.path, data.version ?? null)}
            alt={data.label ?? t("node_canvas_image_label")}
            className="nodrag max-h-[320px] w-full object-contain"
            draggable={false}
          />
        ) : busy ? (
          <span className="flex items-center gap-2 text-[12px] text-text-3">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {data.status === "queued" ? t("node_canvas_image_queued") : t("node_canvas_image_generating")}
          </span>
        ) : failed ? (
          <span className="flex flex-col items-center gap-1 px-3 text-center text-[12px] text-red-300">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {t("node_canvas_image_failed")}
            {data.error ? <span className="line-clamp-4 text-[10.5px] text-text-4">{data.error}</span> : null}
          </span>
        ) : (
          <span className="text-[12px] text-text-4">{t("node_canvas_image_empty")}</span>
        )}
      </div>
      {data.generated ? (
        <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("image", false)} />
      ) : null}
      <Handle type="source" position={Position.Right} id="image" style={handleStyle("image")} />
    </NodeShell>
  );
}

function ImageGenerateNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setAspectRatio, run, remove } = useCanvasActions();
  const busy = isBusy(data.status);
  return (
    <NodeShell
      selected={selected}
      width={280}
      icon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_generate_label")}
      onRemove={busy ? undefined : () => void remove(id)}
    >
      <div className="relative -mx-3 mb-2 h-6">
        <Handle type="target" position={Position.Left} id="prompt" style={handleStyle("text")} />
        <HandleLabel side="left">{t("node_canvas_handle_prompt")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="text" style={handleStyle("text")} />
        <HandleLabel side="right">{t("node_canvas_handle_text")}</HandleLabel>
      </div>
      <div className="relative -mx-3 mb-3 h-6">
        <Handle type="target" position={Position.Left} id="refs" style={handleStyle("image")} />
        <HandleLabel side="left">{t("node_canvas_handle_refs")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="image" style={handleStyle("image")} />
        <HandleLabel side="right">{t("node_canvas_handle_image")}</HandleLabel>
      </div>
      <p className="mb-2 text-[10.5px] leading-snug text-text-4">{t("node_canvas_refs_hint")}</p>
      <label className="mb-3 flex items-center justify-between gap-2 text-[11.5px] text-text-3">
        {t("node_canvas_aspect_ratio")}
        <select
          className={`nodrag ${INPUT_CLS} !w-auto !py-1`}
          value={data.aspect_ratio ?? "9:16"}
          disabled={busy}
          onChange={(e) => void setAspectRatio(id, e.target.value)}
        >
          {ASPECT_RATIOS.map((ratio) => (
            <option key={ratio} value={ratio}>
              {ratio}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className={`nodrag w-full justify-center ${ACCENT_BTN_SM_CLS}`} disabled={busy} onClick={() => void run(id)}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Play className="h-3.5 w-3.5" aria-hidden="true" />}
        {busy ? t("node_canvas_running") : t("node_canvas_run")}
      </button>
      {data.status === "failed" && data.error ? (
        <p className="mt-2 line-clamp-3 text-[10.5px] text-red-300">{data.error}</p>
      ) : null}
    </NodeShell>
  );
}

function StatusPill({ tone, children }: { tone: "ok" | "warn" | "muted"; children: ReactNode }) {
  const cls =
    tone === "ok"
      ? "bg-emerald-400/10 text-emerald-300"
      : tone === "warn"
        ? "bg-amber-400/10 text-amber-300"
        : "bg-white/5 text-text-4";
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}>{children}</span>;
}

function EpisodeGroupView({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      className={`h-full w-full rounded-[16px] border border-dashed bg-white/[0.02] ${selected ? "border-accent/60" : "border-hairline-strong"}`}
    >
      <div className="flex items-center gap-2 px-4 py-3 text-[12px] font-medium text-text-2">
        <Layers className="h-3.5 w-3.5" aria-hidden="true" />
        {t("node_canvas_episode_title", { episode: data.episode })}
        {data.title ? <span className="truncate text-text-4">{data.title}</span> : null}
        <span className="ml-auto text-[11px] text-text-4">{t("node_canvas_episode_units", { count: data.unit_count ?? 0 })}</span>
      </div>
    </div>
  );
}

function UnitNodeView({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  return (
    <NodeShell
      selected={selected}
      width={300}
      icon={<Film className="h-3.5 w-3.5" aria-hidden="true" />}
      title={`${data.unit_id ?? ""}${data.duration_seconds ? ` · ${data.duration_seconds}s` : ""}`}
    >
      <p className="nowheel line-clamp-5 whitespace-pre-wrap text-[11.5px] leading-snug text-text-2">{data.text}</p>
      <Handle type="target" position={Position.Left} id="frame" isConnectable={false} style={handleStyle("image", false)} />
      <Handle type="source" position={Position.Right} id="unit" style={{ ...handleStyle("unit"), top: "35%" }} />
      <Handle type="source" position={Position.Right} id="text" style={{ ...handleStyle("text"), top: "65%" }} />
      <HandleLabel side="right">{t("node_canvas_handle_unit_text")}</HandleLabel>
    </NodeShell>
  );
}

function KeyframeNodeView({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { projectName, runKeyframe, busyNodeIds } = useCanvasActions();
  const nodeKey = `${data.episode}:${data.sheet_id}`;
  const busy = busyNodeIds.has(nodeKey);
  return (
    <NodeShell
      selected={selected}
      width={260}
      icon={<Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_keyframe_title", { id: data.sheet_id })}
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <StatusPill tone="muted">{t("node_canvas_keyframe_cells", { count: data.cell_count ?? 0 })}</StatusPill>
        {data.stale ? <StatusPill tone="warn">{t("node_canvas_stale_short")}</StatusPill> : null}
        {data.approved && !data.stale && data.path ? <StatusPill tone="ok">{t("node_canvas_keyframe_approved")}</StatusPill> : null}
      </div>
      <div className="mb-2 flex min-h-[120px] items-center justify-center overflow-hidden rounded-[8px] border border-hairline bg-black/30">
        {data.path ? (
          <img
            src={API.getFileUrl(projectName, data.path, data.version ?? null)}
            alt={t("node_canvas_keyframe_title", { id: data.sheet_id })}
            className="max-h-[200px] w-full object-contain"
            draggable={false}
          />
        ) : (
          <span className="text-[12px] text-text-4">{t("node_canvas_image_empty")}</span>
        )}
      </div>
      {data.problem ? <p className="mb-2 text-[10.5px] text-amber-300">{t("node_canvas_keyframe_problem", { name: data.problem.name })}</p> : null}
      <button
        type="button"
        className={`nodrag w-full justify-center ${ACCENT_BTN_SM_CLS}`}
        disabled={busy || Boolean(data.problem)}
        onClick={() => void runKeyframe(data.episode ?? 0, data.sheet_id ?? "")}
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Play className="h-3.5 w-3.5" aria-hidden="true" />}
        {busy ? t("node_canvas_running") : data.path ? t("node_canvas_regenerate") : t("node_canvas_run")}
      </button>
      <Handle type="source" position={Position.Right} id="image" style={handleStyle("image")} />
    </NodeShell>
  );
}

function H3PromptNodeView({ data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const status = data.status as string | undefined;
  const tone = status === "current" ? "ok" : status === "stale" ? "warn" : "muted";
  const sections = Object.entries(data.sections ?? {});
  return (
    <NodeShell
      selected={selected}
      width={300}
      icon={<PenLine className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_h3_title", { id: data.unit_id })}
    >
      <div className="mb-2">
        <StatusPill tone={tone}>{t(`node_canvas_h3_status_${status ?? "missing"}`)}</StatusPill>
      </div>
      {sections.length > 0 ? (
        <div className="nodrag nowheel max-h-[220px] space-y-2 overflow-y-auto pr-1">
          {sections.map(([name, body]) => (
            <div key={name}>
              <p className="text-[10px] uppercase tracking-wide text-text-4">{name}</p>
              <p className="whitespace-pre-wrap text-[11px] leading-snug text-text-2">{body}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[11.5px] text-text-4">{t("node_canvas_h3_empty")}</p>
      )}
      <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("h3", false)} />
    </NodeShell>
  );
}

function H3WriterNodeView({ id, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { runWriter, remove, busyNodeIds } = useCanvasActions();
  const busy = busyNodeIds.has(id);
  return (
    <NodeShell
      selected={selected}
      width={260}
      icon={<PenLine className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_h3_writer_label")}
      onRemove={busy ? undefined : () => void remove(id)}
    >
      <div className="relative -mx-3 mb-3 h-6">
        <Handle type="target" position={Position.Left} id="units" style={handleStyle("unit")} />
        <HandleLabel side="left">{t("node_canvas_handle_units")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="h3" style={handleStyle("h3")} />
        <HandleLabel side="right">{t("node_canvas_handle_h3")}</HandleLabel>
      </div>
      <p className="mb-3 text-[10.5px] leading-snug text-text-4">{t("node_canvas_h3_writer_hint")}</p>
      <button type="button" className={`nodrag w-full justify-center ${ACCENT_BTN_SM_CLS}`} disabled={busy} onClick={() => void runWriter(id)}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Play className="h-3.5 w-3.5" aria-hidden="true" />}
        {busy ? t("node_canvas_running") : t("node_canvas_run")}
      </button>
    </NodeShell>
  );
}

export const canvasNodeTypes = {
  episode: memo(EpisodeGroupView),
  unit: memo(UnitNodeView),
  keyframe: memo(KeyframeNodeView),
  h3_prompt: memo(H3PromptNodeView),
  h3_writer: memo(H3WriterNodeView),
  text: memo(TextNodeView),
  image: memo(ImageNodeView),
  image_generate: memo(ImageGenerateNodeView),
};
