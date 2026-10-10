import { memo, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Handle, NodeResizer, Position, useNodeId, useStore, type Node, type NodeProps } from "@xyflow/react";
import { AlertTriangle, AtSign, Package, Image as ImageIcon, Loader2, Mic, Play, Sparkles, Type, Upload, Video, X, ChevronDown, ChevronUp, FileText, Save, Copy } from "lucide-react";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { useClaudeStore } from "@/stores/claude-store";
import type { CanvasAnyNodeType, CanvasHandleType, CanvasNodeData } from "@/types/canvas";
import { ACCENT_BTN_SM_CLS, GHOST_BTN_CLS, ICON_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { ProviderModelSelect } from "@/components/ui/ProviderModelSelect";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { KeyframeSheetRequestDialog } from "@/components/canvas/reference/KeyframeSheetRequestDialog";
import type { KeyframeSheetRequest } from "@/types/keyframe";
import { useBusyNodeIds, useCanvasActions } from "./canvas-context";
import { ASPECT_RATIOS, PASSIVE_INPUT_HANDLE, isBusy } from "./canvas-rules";
import { MarkdownText } from "./MarkdownText";
import { NodeStatusLine } from "./NodeStatusLine";

export type CanvasFlowNode = Node<CanvasNodeData, CanvasAnyNodeType>;

const HANDLE_COLOR: Record<CanvasHandleType, string> = {
  text: "var(--color-accent)",
  image: "oklch(0.74 0.13 190)",
  video: "oklch(0.72 0.15 25)",
  audio: "oklch(0.78 0.12 320)",
  asset: "oklch(0.8 0.14 55)",
  doc: "oklch(0.78 0.04 250)",
};
const TEXT_SAVE_DELAY_MS = 600;

/** 缩得很小时视频 / 音频不挂载：几十个媒体元素同时解码是缩放卡顿的主因。订阅布尔值，只在跨过阈值时重绘。 */
const LOW_ZOOM = 0.45;
const selectLowZoom = (state: { transform: [number, number, number] }) => state.transform[2] < LOW_ZOOM;
function useLowZoom(): boolean {
  return useStore(selectLowZoom);
}

const HANDLE_STYLES = new Map<string, CSSProperties>();

/** 同一 (类型, 可连接) 恒返回同一个对象：Handle 的 style 引用稳定，重绘时不会因新对象而被判定为变化。 */
function handleStyle(type: CanvasHandleType, connectable = true): CSSProperties {
  const key = `${type}:${connectable}`;
  let style = HANDLE_STYLES.get(key);
  if (!style) {
    style = {
      width: 12,
      height: 12,
      background: HANDLE_COLOR[type],
      border: "2px solid oklch(0.16 0.010 265)",
      ...(connectable ? {} : { opacity: 0.35 }),
    };
    HANDLE_STYLES.set(key, style);
  }
  return style;
}

function NodeShell({
  selected,
  width,
  icon,
  title,
  onRemove,
  onDuplicate,
  compact,
  children,
}: {
  selected: boolean;
  width: number;
  icon: ReactNode;
  title: string;
  onRemove?: () => void;
  onDuplicate?: () => void;
  /** 流水线节点：`true` 显示成紧凑卡片，`false` 展开，`undefined` 表示这个节点没有紧凑形态。 */
  compact?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  const nodeId = useNodeId();
  const { addToContext, setData } = useCanvasActions();
  return (
    <div
      style={{ width }}
      className={`rounded-[12px] border bg-bg-grad-a shadow-lg ${
        selected ? "border-accent/70" : "border-hairline"
      }`}
    >
      <div className="flex items-center gap-1.5 border-b border-hairline px-3 py-1.5 text-[12px] font-semibold text-text-2">
        {icon}
        <span className="flex-1 truncate">{title}</span>
        {nodeId && compact !== undefined && (
          <button
            type="button"
            className={`nodrag ${ICON_BTN_CLS}`}
            onClick={() => void setData(nodeId, { compact: !compact })}
            title={compact ? t("node_canvas_expand") : t("node_canvas_collapse")}
            aria-label={compact ? t("node_canvas_expand") : t("node_canvas_collapse")}
          >
            {compact ? <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />}
          </button>
        )}
        {nodeId && (
          <button
            type="button"
            className={`nodrag ${ICON_BTN_CLS}`}
            onClick={() => addToContext(nodeId)}
            title={t("node_canvas_add_context")}
            aria-label={t("node_canvas_add_context")}
          >
            <AtSign className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
        {onDuplicate && (
          <button
            type="button"
            className={`nodrag ${ICON_BTN_CLS}`}
            onClick={onDuplicate}
            title={t("node_canvas_duplicate")}
            aria-label={t("node_canvas_duplicate")}
          >
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
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
    <span className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-[10.5px] text-text-3 ${side === "left" ? "left-4" : "right-4"}`}>
      {children}
    </span>
  );
}


/** 画布上的图片：双击放大查看（Esc 或点背景关闭）。 */
function CanvasImage({ src, alt, className }: { src: string; alt: string; className: string }) {
  // 节点里只显示小图（服务端缩放并缓存）；双击放大才载入原图。data: 占位图不带参数。
  const thumb = src.startsWith("data:") ? src : `${src}${src.includes("?") ? "&" : "?"}w=480`;
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  return (
    <>
      <img
        src={thumb}
        alt={alt}
        title={t("node_canvas_image_zoom_hint")}
        className={`${className} cursor-zoom-in`}
        draggable={false}
        loading="lazy"
        decoding="async"
        onDoubleClick={() => setOpen(true)}
      />
      {open ? <ImageLightbox src={src} alt={alt} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/** 图片的参考说明：这张图被别的节点当参考图时，说明连同图一起交给供应商（如「This is his face…」）。 */
function NoteField({ id, note }: { id: string; note: string | undefined }) {
  const { t } = useTranslation("dashboard");
  const { setNote } = useCanvasActions();
  const [draft, setDraft] = useState(note ?? "");
  const dirty = useRef(false);

  useEffect(() => {
    if (!dirty.current) setDraft(note ?? "");
  }, [note]);

  const commit = () => {
    if (!dirty.current) return;
    dirty.current = false;
    void setNote(id, draft);
  };

  return (
    <label className="mt-2 block">
      <span className="mb-1 block text-[10.5px] text-text-4">{t("node_canvas_note_label")}</span>
      <textarea
        className={`nodrag nowheel ${INPUT_CLS} min-h-[44px] resize-none text-[11px]`}
        rows={2}
        value={draft}
        placeholder={t("node_canvas_note_placeholder")}
        onChange={(e) => {
          dirty.current = true;
          setDraft(e.target.value);
        }}
        onBlur={commit}
      />
    </label>
  );
}

function TextNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setText, remove, saveUnitField } = useCanvasActions();
  const [draft, setDraft] = useState(data.text ?? "");
  const compact = data.step ? Boolean(data.compact) : undefined;
  // 单元文本 / 导演说明：画布上改了，要点「存回剧本」才会写进剧本。
  const scriptField = data.step === "script" ? "text" : data.step === "direction" ? "direction" : null;
  const unsaved = scriptField !== null && data.script_value !== undefined && draft !== data.script_value;
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const maxHeight = compact ? 280 : 520;
  // 文字多就往下长（到上限再滚动），不用手动拖大。
  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight + 2, maxHeight)}px`;
  }, [draft, maxHeight]);

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
      width={data.step ? 300 : 280}
      icon={<Type className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.generated ? t("node_canvas_text_generated") : (data.label ?? t("node_canvas_text_label"))}
      onRemove={data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      {data.step === "h3" && data.h3_status && data.h3_status !== "current" ? (
        <p className="mb-1.5 text-[10.5px] text-amber-300">{t(`node_canvas_h3_status_${data.h3_status}`)}</p>
      ) : null}
      <textarea
        ref={areaRef}
        className={`nodrag nowheel ${INPUT_CLS} ${compact ? "min-h-[80px]" : "min-h-[96px]"} resize-y`}
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
      {unsaved && scriptField && data.ep !== undefined && data.unit ? (
        <button
          type="button"
          className={`nodrag mt-2 w-full justify-center ${GHOST_BTN_CLS}`}
          title={t("node_canvas_save_to_script_hint")}
          onClick={() => {
            if (window.confirm(t("node_canvas_save_to_script_confirm"))) void saveUnitField(data.ep ?? 0, data.unit ?? "", scriptField, draft);
          }}
        >
          <Save className="h-3.5 w-3.5" aria-hidden="true" />
          {t("node_canvas_save_to_script")}
        </button>
      ) : null}
      {data.generated ? (
        <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("text", false)} />
      ) : null}
      <Handle type="source" position={Position.Right} id="text" style={handleStyle("text")} />
    </NodeShell>
  );
}

function ImageNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { projectName, remove, uploadImage } = useCanvasActions();
  const uploadInput = useRef<HTMLInputElement>(null);
  const busy = isBusy(data.status);
  const failed = data.status === "failed";
  const compact = data.step ? Boolean(data.compact) : undefined;
  return (
    <NodeShell
      selected={selected}
      width={compact ? 220 : 240}
      icon={<ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.label ?? t("node_canvas_image_label")}
      onRemove={busy || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      {data.stale ? (
        <p className="mb-2 flex items-center gap-1.5 rounded-[6px] bg-amber-400/10 px-2 py-1 text-[10.5px] text-amber-300">
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          {t("node_canvas_stale")}
        </p>
      ) : null}
      <div className={`flex ${compact ? "min-h-[64px]" : "min-h-[160px]"} items-center justify-center overflow-hidden rounded-[8px] border border-dashed border-hairline bg-black/30`}>
        {data.path ? (
          <CanvasImage
            src={API.getFileUrl(projectName, data.path, data.version ?? null)}
            alt={data.label ?? t("node_canvas_image_label")}
            className={`nodrag ${compact ? "max-h-[96px]" : "max-h-[320px]"} w-full object-contain`}
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
      {data.generated ? <NodeStatusLine data={data} /> : null}
      {data.path && !compact ? <NoteField id={id} note={data.note} /> : null}
      {data.generated || compact ? null : (
        <>
          <input
            ref={uploadInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void uploadImage(id, file);
              e.target.value = "";
            }}
          />
          <button type="button" className={`nodrag mt-2 w-full justify-center ${GHOST_BTN_CLS}`} onClick={() => uploadInput.current?.click()}>
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            {data.path ? t("node_canvas_image_replace") : t("node_canvas_image_upload")}
          </button>
        </>
      )}
      {data.generated ? (
        <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("image", false)} />
      ) : null}
      <Handle type="source" position={Position.Right} id="image" style={handleStyle("image")} />
    </NodeShell>
  );
}

/**
 * 图片供应商节点（仿 RunningHub 工作流节点）：输入 image1（参考图）与 prompt，输出一张图。
 * 提示词里的 `@资产名` 运行时换成「图N」并自动带上该资产的图与说明。
 */
/** 起始帧联合图节点上的参考图缩略条：与「生成前核对」弹窗同一份数据，点开看提示词；接线变了就重新读。 */
export function KeyframeRefsStrip({ episode, sheetId, nodeId }: { episode: number; sheetId: string; nodeId: string }) {
  const { t } = useTranslation("dashboard");
  const { projectName } = useCanvasActions();
  const lowZoom = useLowZoom();
  const wired = useStore((state) =>
    state.edges
      .filter((e) => e.target === nodeId && e.targetHandle === "image1")
      .map((e) => e.source)
      .sort()
      .join(","),
  );
  const [loaded, setLoaded] = useState<{ key: string; request: KeyframeSheetRequest | null; failed: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const key = `${projectName}:${episode}:${sheetId}:${wired}`;
  useEffect(() => {
    if (lowZoom) return;
    const controller = new AbortController();
    API.getKeyframeSheetRequest(projectName, episode, sheetId, { signal: controller.signal })
      .then((request) => {
        if (!controller.signal.aborted) setLoaded({ key, request, failed: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoaded({ key, request: null, failed: true });
      });
    return () => controller.abort();
  }, [projectName, episode, sheetId, key, lowZoom]);
  const current = loaded?.key === key ? loaded : null;
  const request = current?.request ?? null;
  const references = request && request.problem === null ? request.references : [];
  return (
    <div className="nodrag mb-2">
      <button type="button" className={`w-full justify-start ${GHOST_BTN_CLS}`} onClick={() => setOpen(true)}>
        {request?.problem != null
          ? t("node_canvas_keyframe_refs_blocked", { name: request.problem.name })
          : current?.failed
            ? t("node_canvas_keyframe_refs_failed")
            : t("node_canvas_keyframe_refs", { count: references.length })}
      </button>
      {references.length > 0 ? (
        <ol className="mt-1.5 flex flex-wrap gap-1">
          {references.map((reference, index) => (
            <li
              key={`${index}-${reference.path}`}
              title={reference.label}
              className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-[5px] bg-black/40"
            >
              {reference.path ? (
                <img src={API.getFileUrl(projectName, reference.path)} alt={reference.label} className="h-full w-full object-cover" loading="lazy" />
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {open ? <KeyframeSheetRequestDialog projectName={projectName} episode={episode} sheetId={sheetId} onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

function ImageGenerateNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setAspectRatio, setModel, imageModels, run, remove } = useCanvasActions();
  const busyIds = useBusyNodeIds();
  const busy = isBusy(data.status) || busyIds.has(id);
  const compact = data.step ? Boolean(data.compact) : undefined;
  // 起始帧联合图：提示词与角色 / 场景参考由项目规划决定，只有 image1 能额外加参考图。
  const pipeline = data.step === "kf_gen";
  return (
    <NodeShell
      selected={selected}
      width={280}
      icon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
      title={typeof data.title === "string" && data.title ? data.title : t("node_canvas_generate_label")}
      onRemove={busy || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      <div className="relative -mx-3 mb-2 h-6">
        <Handle type="target" position={Position.Left} id="image1" style={handleStyle("image")} />
        <HandleLabel side="left">{t(pipeline ? "node_canvas_handle_extra_refs" : "node_canvas_handle_image1")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="image" style={handleStyle("image")} />
        <HandleLabel side="right">{t("node_canvas_handle_image")}</HandleLabel>
      </div>
      {pipeline ? null : (
        <div className="relative -mx-3 mb-3 h-6">
          <Handle type="target" position={Position.Left} id="prompt" style={handleStyle("text")} />
          <HandleLabel side="left">{t("node_canvas_handle_prompt")}</HandleLabel>
        </div>
      )}
      {pipeline && typeof data.ep === "number" && data.sheet ? <KeyframeRefsStrip episode={data.ep} sheetId={data.sheet} nodeId={id} /> : null}
      {compact ? null : <p className="mb-2 text-[10.5px] leading-snug text-text-4">{t(pipeline ? "node_canvas_pipeline_keyframe_hint" : "node_canvas_mention_hint")}</p>}
      <div className={`nodrag mb-2 ${compact ? "hidden" : ""}`}>
        <ProviderModelSelect
          value={data.model ?? ""}
          options={imageModels.options}
          providerNames={imageModels.providerNames}
          modelNames={imageModels.modelNames}
          onChange={(value) => void setModel(id, value)}
          allowDefault
          defaultLabel={t("node_canvas_model_default")}
          aria-label={t("node_canvas_model_label")}
        />
      </div>
      <label className={`mb-3 flex items-center justify-between gap-2 text-[11.5px] text-text-3 ${compact ? "hidden" : ""}`}>
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
      <NodeStatusLine data={data} />
      {data.status === "failed" && data.error ? (
        <p className="mt-2 line-clamp-3 text-[10.5px] text-red-300">{data.error}</p>
      ) : null}
    </NodeShell>
  );
}

/**
 * 资产节点：接一张图（可以空着，等图来），一个 `asset` 输出口把「图 + 参考说明」交给下游当参考。
 * 放在资产分组里时，它接着的图也就是这个资产在项目里的资产图。
 */
function AssetNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { projectName, remove, duplicate, setData } = useCanvasActions();
  const name = data.derivative ? `${data.name ?? ""}/${data.derivative}` : (data.name ?? "");
  const compact = Boolean(data.compact);
  const [draft, setDraft] = useState(data.name ?? "");
  const dirty = useRef(false);
  useEffect(() => {
    if (!dirty.current) setDraft(data.name ?? "");
  }, [data.name]);
  const image = data.path ? (
    <CanvasImage
      src={API.getFileUrl(projectName, data.path, data.version ?? null)}
      alt={name}
      className={`nodrag ${compact ? "h-10 w-10" : "max-h-[120px] w-full"} object-contain`}
    />
  ) : null;
  return (
    <NodeShell
      selected={selected}
      width={compact ? 200 : 260}
      icon={<Package className="h-3.5 w-3.5" aria-hidden="true" />}
      title={name ? t("node_canvas_asset_label", { name }) : t("node_canvas_asset_plain")}
      onRemove={() => void remove(id)}
      onDuplicate={() => void duplicate(id)}
      compact={compact}
    >
      {data.standalone && !data.copy_of ? (
        <input
          className={`nodrag mb-2 w-full ${INPUT_CLS}`}
          value={draft}
          placeholder={t("node_canvas_asset_name_placeholder")}
          aria-label={t("node_canvas_asset_name_placeholder")}
          onChange={(e) => {
            dirty.current = true;
            setDraft(e.target.value);
          }}
          onBlur={() => {
            dirty.current = false;
            if (draft !== (data.name ?? "")) void setData(id, { name: draft });
          }}
        />
      ) : null}
      {compact ? (
        <div className="relative -mx-3 flex min-h-[40px] items-center gap-2 px-3">
          <Handle type="target" position={Position.Left} id="image" style={handleStyle("image")} />
          {image ?? <span className="text-[12px] text-text-4">{t("node_canvas_asset_empty")}</span>}
        </div>
      ) : (
        <>
          <div className="relative -mx-3 mb-2 h-6">
            <Handle type="target" position={Position.Left} id="image" style={handleStyle("image")} />
            <HandleLabel side="left">{t("node_canvas_handle_image")}</HandleLabel>
          </div>
          <div className="flex min-h-[80px] items-center justify-center overflow-hidden rounded-[8px] border border-dashed border-hairline bg-black/30">
            {image ?? <span className="text-[12px] text-text-4">{t("node_canvas_asset_empty")}</span>}
          </div>
          <NoteField id={id} note={data.note} />
        </>
      )}
      <Handle type="source" position={Position.Right} id="asset" style={handleStyle("asset")} />
    </NodeShell>
  );
}

/** 分组：标题可改、可缩放；删掉分组框只是拆组，里面的节点保留。资产分组的标题就是资产名。 */
function GroupView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setData, resizeGroup, remove, addToContext, tidyGroup } = useCanvasActions();
  const [draft, setDraft] = useState(data.label ?? "");
  const dirty = useRef(false);
  useEffect(() => {
    if (!dirty.current) setDraft(data.label ?? "");
  }, [data.label]);
  return (
    <div className={`h-full w-full rounded-[16px] border border-dashed bg-white/[0.025] ${selected ? "border-accent/70" : "border-hairline-strong"}`}>
      <NodeResizer
        isVisible={selected}
        minWidth={240}
        minHeight={160}
        onResizeEnd={(_, params) => void resizeGroup(id, { x: params.x, y: params.y }, params.width, params.height)}
      />
      <div className="flex items-center gap-2 px-4 py-2.5">
        <input
          className="nodrag min-w-0 flex-1 bg-transparent text-[15px] font-semibold text-text placeholder:text-text-4 focus-visible:outline-none"
          value={draft}
          placeholder={t("node_canvas_group_label")}
          aria-label={t("node_canvas_group_label")}
          onChange={(e) => {
            dirty.current = true;
            setDraft(e.target.value);
          }}
          onBlur={() => {
            dirty.current = false;
            if (draft !== (data.label ?? "")) void setData(id, { label: draft });
          }}
        />
        {data.asset ? (
          <span className="shrink-0 rounded-full border border-hairline px-2 py-0.5 text-[10.5px] font-medium text-text-3" title={t("node_canvas_group_asset_hint")}>
            {t(`node_canvas_group_kind_${data.asset}`)}
          </span>
        ) : null}
        <button type="button" className={`nodrag ${GHOST_BTN_CLS}`} onClick={() => void tidyGroup(id)}>
          {t("node_canvas_group_tidy")}
        </button>
        <button
          type="button"
          className={`nodrag ${ICON_BTN_CLS}`}
          onClick={() => addToContext(id)}
          title={t("node_canvas_add_context")}
          aria-label={t("node_canvas_add_context")}
        >
          <AtSign className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button type="button" className={`nodrag ${GHOST_BTN_CLS}`} onClick={() => void remove(id)}>
          {t("node_canvas_ungroup")}
        </button>
      </div>
    </div>
  );
}

const DURATIONS = [4, 5, 6, 8, 10, 12, 15] as const;

/** 运行产出的视频 / 语音节点的共同外壳：占位、失败、播放器三态。 */
function MediaOutputBody({
  data,
  kind,
  compact = false,
}: {
  data: CanvasNodeData;
  kind: "video" | "audio";
  compact?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const { projectName } = useCanvasActions();
  const busy = isBusy(data.status);
  const lowZoom = useLowZoom();
  if (data.path && lowZoom) {
    return <div className={`${kind === "video" ? (compact ? "h-[110px]" : "h-[200px]") : "h-[54px]"} w-full rounded-[8px] bg-white/5`} />;
  }
  if (data.path) {
    const src = API.getFileUrl(projectName, data.path, data.version ?? null);
    return kind === "video" ? (
      // eslint-disable-next-line jsx-a11y/media-has-caption -- 用户自己生成的预览，没有字幕源
      <video src={src} controls preload="none" className={`nodrag ${compact ? "max-h-[110px]" : "max-h-[320px]"} w-full rounded-[8px] bg-black/30`} />
    ) : (
      // eslint-disable-next-line jsx-a11y/media-has-caption -- 合成语音的试听，文本就在上游文本节点里
      <audio src={src} controls preload="none" className="nodrag w-full" />
    );
  }
  if (busy) {
    return (
      <span className="flex items-center gap-2 text-[12px] text-text-3">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {data.status === "queued" ? t("node_canvas_image_queued") : t("node_canvas_image_generating")}
      </span>
    );
  }
  if (data.status === "failed") {
    return (
      <span className="flex flex-col gap-1 text-[12px] text-red-300">
        <span className="flex items-center gap-1.5">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          {t("node_canvas_image_failed")}
        </span>
        {data.error ? <span className="line-clamp-4 text-[10.5px] text-text-4">{data.error}</span> : null}
      </span>
    );
  }
  return <span className="text-[12px] text-text-4">{t("node_canvas_media_empty")}</span>;
}

function VideoNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { remove } = useCanvasActions();
  const compact = data.step ? Boolean(data.compact) : undefined;
  return (
    <NodeShell
      selected={selected}
      width={compact ? 240 : 280}
      icon={<Video className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.label ? `${data.label} · ${t("node_canvas_video_label")}` : t("node_canvas_video_label")}
      onRemove={isBusy(data.status) || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      <MediaOutputBody data={data} kind="video" compact={compact} />
      <NodeStatusLine data={data} />
      <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("video", false)} />
      <Handle type="source" position={Position.Right} id="video" style={handleStyle("video")} />
    </NodeShell>
  );
}

function AudioNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { remove } = useCanvasActions();
  const compact = data.step ? Boolean(data.compact) : undefined;
  return (
    <NodeShell
      selected={selected}
      width={compact ? 240 : 280}
      icon={<Mic className="h-3.5 w-3.5" aria-hidden="true" />}
      title={data.label ? `${data.label} · ${t("node_canvas_audio_label")}` : t("node_canvas_audio_label")}
      onRemove={isBusy(data.status) || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      <MediaOutputBody data={data} kind="audio" compact={compact} />
      <NodeStatusLine data={data} />
      <Handle type="target" position={Position.Left} id={PASSIVE_INPUT_HANDLE} isConnectable={false} style={handleStyle("audio", false)} />
      <Handle type="source" position={Position.Right} id="audio" style={handleStyle("audio")} />
    </NodeShell>
  );
}

/** 文档节点：项目里一份文本 / markdown 文件（来源、分集计划…）的只读预览，点开看全文。 */
function MarkdownNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { projectName, openMarkdown, remove } = useCanvasActions();
  const [preview, setPreview] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const file = data.file ?? "";
  const fileRev = data.file_rev;
  const episode = data.step === "screenplay" ? data.ep : undefined;
  const status = data.screenplay_status ?? "none";
  const writeWithAgent = async () => {
    if (episode == null) return;
    setWorking(true);
    setProblem(null);
    try {
      await useClaudeStore.getState().ask(projectName, { action: "create_screenplay", episode });
    } catch (error) {
      setProblem(errMsg(error));
    } finally {
      setWorking(false);
    }
  };
  const approve = async () => {
    if (episode == null || !data.screenplay_fingerprint) return;
    setWorking(true);
    setProblem(null);
    try {
      await API.approveScreenplay(projectName, episode, data.screenplay_fingerprint);
    } catch (error) {
      setProblem(errMsg(error));
    } finally {
      setWorking(false);
    }
  };
  useEffect(() => {
    if (!file) return;
    const controller = new AbortController();
    API.getCanvasMarkdown(projectName, file)
      .then((res) => {
        if (!controller.signal.aborted) setPreview(res.content);
      })
      .catch(() => {
        if (!controller.signal.aborted) setPreview("");
      });
    return () => controller.abort();
  }, [file, fileRev, projectName]);
  const title = data.title || file;
  return (
    <NodeShell
      selected={selected}
      width={300}
      icon={<FileText className="h-3.5 w-3.5" aria-hidden="true" />}
      title={title}
      onRemove={data.step ? undefined : () => void remove(id)}
    >
      <div className="relative max-h-[260px] min-h-[120px] overflow-hidden">
        <MarkdownText
          className="text-[11.5px]"
          source={preview === null ? t("node_canvas_loading") : preview.trim() || t("node_canvas_markdown_empty")}
        />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-bg-grad-a to-transparent" />
      </div>
      {episode != null ? (
        <p className="mt-2 text-[11px] text-text-3">{t(`node_canvas_screenplay_${status}`)}</p>
      ) : null}
      <button type="button" className={`nodrag mt-2 w-full justify-center ${GHOST_BTN_CLS}`} onClick={() => openMarkdown(file, title)}>
        {t("node_canvas_markdown_open")}
      </button>
      {episode != null ? (
        <div className="mt-1.5 flex gap-1.5">
          <button type="button" className={`nodrag flex-1 justify-center ${ACCENT_BTN_SM_CLS}`} disabled={working} onClick={() => void writeWithAgent()}>
            {working ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
            {status === "none" ? t("node_canvas_screenplay_write") : t("node_canvas_screenplay_rewrite")}
          </button>
          {status === "pending_review" ? (
            <button type="button" className={`nodrag flex-1 justify-center ${GHOST_BTN_CLS}`} disabled={working} onClick={() => void approve()}>
              {t("node_canvas_screenplay_approve")}
            </button>
          ) : null}
        </div>
      ) : null}
      {problem ? <p className="mt-1.5 text-[11px] text-red-300">{problem}</p> : null}
      <Handle type="target" position={Position.Left} id="in" style={handleStyle("doc")} />
      <Handle type="source" position={Position.Right} id="out" style={handleStyle("doc")} />
    </NodeShell>
  );
}

function RunButton({ id, busy }: { id: string; busy: boolean }) {
  const { t } = useTranslation("dashboard");
  const { run } = useCanvasActions();
  return (
    <button type="button" className={`nodrag w-full justify-center ${ACCENT_BTN_SM_CLS}`} disabled={busy} onClick={() => void run(id)}>
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Play className="h-3.5 w-3.5" aria-hidden="true" />}
      {busy ? t("node_canvas_running") : t("node_canvas_run")}
    </button>
  );
}

function VideoGenerateNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setAspectRatio, setData, remove } = useCanvasActions();
  const busyIds = useBusyNodeIds();
  const busy = isBusy(data.status) || busyIds.has(id);
  const compact = data.step ? Boolean(data.compact) : undefined;
  // 单元视频：提示词与起始帧取自项目脚本与起始帧看板，画布上的连线只用来展示，不能改。
  const fed = Boolean(data.step);
  return (
    <NodeShell
      selected={selected}
      width={280}
      icon={<Video className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_video_generate_label")}
      onRemove={busy || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      <div className="relative -mx-3 mb-2 h-6">
        <Handle type="target" position={Position.Left} id="prompt" isConnectable={!fed} style={handleStyle("text", !fed)} />
        <HandleLabel side="left">{t("node_canvas_handle_prompt")}</HandleLabel>
      </div>
      <div className="relative -mx-3 mb-3 h-6">
        <Handle type="target" position={Position.Left} id="frame" isConnectable={!fed} style={handleStyle("image", !fed)} />
        <HandleLabel side="left">{t("node_canvas_handle_frame")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="video" style={handleStyle("video")} />
        <HandleLabel side="right">{t("node_canvas_handle_video")}</HandleLabel>
      </div>
      {compact ? null : <p className="mb-2 text-[10.5px] leading-snug text-text-4">{t(fed ? "node_canvas_pipeline_fed_hint" : "node_canvas_frame_hint")}</p>}
      <div className={`mb-3 grid grid-cols-2 gap-2 text-[11.5px] text-text-3 ${compact ? "hidden" : ""}`}>
        <label className="flex flex-col gap-1">
          {t("node_canvas_aspect_ratio")}
          <select className={`nodrag ${INPUT_CLS} !py-1`} value={data.aspect_ratio ?? "9:16"} disabled={busy} onChange={(e) => void setAspectRatio(id, e.target.value)}>
            {ASPECT_RATIOS.map((ratio) => (
              <option key={ratio} value={ratio}>
                {ratio}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("node_canvas_duration")}
          <select
            className={`nodrag ${INPUT_CLS} !py-1`}
            value={data.duration_seconds ?? 5}
            disabled={busy}
            onChange={(e) => void setData(id, { duration_seconds: Number(e.target.value) })}
          >
            {DURATIONS.map((seconds) => (
              <option key={seconds} value={seconds}>
                {seconds}s
              </option>
            ))}
          </select>
        </label>
      </div>
      <RunButton id={id} busy={busy} />
      <NodeStatusLine data={data} />
      {data.status === "failed" && data.error ? <p className="mt-2 line-clamp-3 text-[10.5px] text-red-300">{data.error}</p> : null}
    </NodeShell>
  );
}

function AudioGenerateNodeView({ id, data, selected }: NodeProps<CanvasFlowNode>) {
  const { t } = useTranslation("dashboard");
  const { setData, remove } = useCanvasActions();
  const busyIds = useBusyNodeIds();
  const busy = isBusy(data.status) || busyIds.has(id);
  const compact = data.step ? Boolean(data.compact) : undefined;
  const [voice, setVoice] = useState(data.voice ?? "");
  // 配音：台词取自项目脚本，画布上的连线只用来展示，不能改。
  const fed = Boolean(data.step);
  return (
    <NodeShell
      selected={selected}
      width={260}
      icon={<Mic className="h-3.5 w-3.5" aria-hidden="true" />}
      title={t("node_canvas_audio_generate_label")}
      onRemove={busy || data.step ? undefined : () => void remove(id)}
      compact={compact}
    >
      <div className="relative -mx-3 mb-3 h-6">
        <Handle type="target" position={Position.Left} id="text" isConnectable={!fed} style={handleStyle("text", !fed)} />
        <HandleLabel side="left">{t("node_canvas_handle_script_text")}</HandleLabel>
        <Handle type="source" position={Position.Right} id="audio" style={handleStyle("audio")} />
        <HandleLabel side="right">{t("node_canvas_handle_audio")}</HandleLabel>
      </div>
      {compact || !fed ? null : <p className="mb-2 text-[10.5px] leading-snug text-text-4">{t("node_canvas_pipeline_fed_hint")}</p>}
      <label className={`mb-3 flex flex-col gap-1 text-[11.5px] text-text-3 ${compact ? "hidden" : ""}`}>
        {t("node_canvas_voice")}
        <input
          className={`nodrag ${INPUT_CLS} !py-1`}
          value={voice}
          disabled={busy}
          placeholder={t("node_canvas_voice_placeholder")}
          onChange={(e) => setVoice(e.target.value)}
          onBlur={() => {
            if (voice !== (data.voice ?? "")) void setData(id, { voice: voice.trim() || null });
          }}
        />
      </label>
      <RunButton id={id} busy={busy} />
      <NodeStatusLine data={data} />
      {data.status === "failed" && data.error ? <p className="mt-2 line-clamp-3 text-[10.5px] text-red-300">{data.error}</p> : null}
    </NodeShell>
  );
}

export const canvasNodeTypes = {
  video: memo(VideoNodeView),
  audio: memo(AudioNodeView),
  video_generate: memo(VideoGenerateNodeView),
  audio_generate: memo(AudioGenerateNodeView),
  text: memo(TextNodeView),
  image: memo(ImageNodeView),
  image_generate: memo(ImageGenerateNodeView),
  asset: memo(AssetNodeView),
  group: memo(GroupView),
  markdown: memo(MarkdownNodeView),
};
