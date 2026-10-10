import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearch } from "wouter";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useEdgesState,
  useKeyPress,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type OnBeforeDelete,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { BoxSelect, Group, Library, UserPlus, ImagePlus, LayoutGrid, Loader2, Mic, Save, Sparkles, Trash2, Type, Video, Workflow, Download, ListChecks } from "lucide-react";
import { API } from "@/api";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { enqueueAutoDub, enqueueKeyframeSheet, enqueueReferenceVideoUnit } from "@/actions/generation";
import { isActiveStatus, useTaskRowsByIds } from "@/stores/tasks-store";
import { FlowModal } from "./FlowModal";
import { MarkdownModal } from "./MarkdownModal";
import { useAppStore } from "@/stores/app-store";
import { useClaudeStore } from "@/stores/claude-store";
import { useCanvasContextStore } from "@/stores/canvas-context-store";
import { errMsg } from "@/utils/async";
import type { CanvasBranchStep, CanvasGraph, CanvasNodeData, CanvasNodeType } from "@/types/canvas";
import { ACCENT_BTN_SM_CLS, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import {
  CanvasActionsContext,
  type CanvasActions,
  type ImageModelChoices,
  CanvasBusyContext,
} from "./canvas-context";
import { tidyEpisodeGroup, tidyLayout, type NodeSizes } from "./canvas-layout";
import { layoutGroup } from "./canvas-elk";
import { BranchStepFailed, runBranch } from "./branch-runner";
import { buildAgentNote, isValidCanvasConnection, lineageOf, referencedNodeIds } from "./canvas-rules";
import { canvasNodeTypes, type CanvasFlowNode } from "./CanvasNodes";
import { useCanvasGraph } from "./use-canvas-graph";

const NEW_NODE_JITTER_PX = 24;

const DIMMED_OPACITY = 0.28;

/** 取景留白（px）：顶部让出工具栏（两行按钮），底部让出 Agent 输入框。 */
const FRAME_PAD = { top: 150, right: 48, bottom: 100, left: 48 } as const;
/** 取景缩放下限：内容再大也不缩到看不清字，超出的部分靠平移。 */
const FRAME_MIN_ZOOM = 0.7;
/** 小地图抬高一截，让出右下角的 Agent 按钮。 */
const DELETE_KEYS = ["Backspace", "Delete"];
/** 按住这些键拖动 = 框选一片区域（Mac 的 Cmd、其他系统的 Ctrl，Shift 同样可用）。 */
/** React Flow 给 group 类型的节点自带灰底和边框；分组框的外观由组件自己画，这里清掉。 */
const GROUP_FRAME_RESET = { background: "transparent", border: "none", padding: 0, boxShadow: "none" } as const;
const GROUP_NODE_TYPES: ReadonlySet<string> = new Set(["group"]);
const SELECTION_KEYS = ["Meta", "Control", "Shift"];
/** 选择模式下仍可用鼠标中键 / 右键拖动画布。 */
const PAN_BUTTONS = [1, 2];
const GROUP_PADDING = 40;
/** 组内节点尺寸连续这么久没变，才认为图片等内容加载完、可以排版。 */
const AUTOLAYOUT_SETTLE_MS = 800;
const GROUP_HEADER = 52;
const DEFAULT_EDGE_OPTIONS = { style: { strokeWidth: 1.8, stroke: "rgba(255,255,255,0.62)" } } as const;
const MINIMAP_STYLE = { marginBottom: 76 } as const;
/** 工具栏按功能分成四组（创建 / 供应商 / 项目资产 / 视图），组内按钮扁平、组与组之间靠外框分开。 */
const CLUSTER_CLS =
  "pointer-events-auto flex items-center gap-0.5 rounded-[11px] border border-hairline bg-bg-grad-a/85 p-1 shadow-lg backdrop-blur";
const TOOL_BTN_CLS =
  "inline-flex items-center gap-1.5 rounded-[8px] px-2.5 py-1.5 text-[12px] font-medium text-text-2 transition-colors hover:bg-white/10 hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

/** 节点内容签名：服务端数据没变（位置、data、分组）就沿用上一个节点对象，React Flow 不会重绘它。 */
function nodeSignature(node: CanvasGraph["nodes"][number]): string {
  return JSON.stringify([node.type, node.position, node.parentId, node.width, node.height, node.data]);
}

function toFlowNodes(
  graph: CanvasGraph,
  previous: readonly CanvasFlowNode[],
  signatures: Map<string, string>,
): CanvasFlowNode[] {
  const local = new Map(previous.map((n) => [n.id, n]));
  const next = graph.nodes.map((node) => {
    const existing = local.get(node.id);
    const signature = nodeSignature(node);
    // 拖动中的位置属于本地：服务端只在拖动结束后才会收到新位置。
    if (existing && (existing.dragging || signatures.get(node.id) === signature)) {
      signatures.set(node.id, existing.dragging ? (signatures.get(node.id) ?? signature) : signature);
      return existing;
    }
    signatures.set(node.id, signature);
    const group = node.type === "group";
    const flowNode: CanvasFlowNode = {
      id: node.id,
      type: node.type,
      position: node.position,
      data: node.data,
      selected: existing?.selected ?? false,
      ...(node.parentId ? { parentId: node.parentId } : {}),
      ...(group
        ? { style: { ...GROUP_FRAME_RESET, width: node.data.width, height: node.data.height }, zIndex: -1 }
        : {}),
    };
    return flowNode;
  });
  for (const id of [...signatures.keys()]) if (!graph.nodes.some((n) => n.id === id)) signatures.delete(id);
  const unchanged = next.length === previous.length && next.every((n, i) => n === previous[i]);
  return unchanged ? (previous as CanvasFlowNode[]) : next;
}

function toFlowEdges(graph: CanvasGraph, previous: readonly Edge[]): Edge[] {
  const statusOf = new Map(graph.nodes.map((n) => [n.id, n.data.status]));
  const before = new Map(previous.map((e) => [e.id, e]));
  const next = graph.edges.map((e) => {
    const animated = statusOf.get(e.target) === "running";
    const existing = before.get(e.id);
    if (
      existing &&
      existing.source === e.source &&
      existing.target === e.target &&
      existing.sourceHandle === e.sourceHandle &&
      existing.targetHandle === e.targetHandle &&
      Boolean(existing.animated) === animated
    ) {
      return existing;
    }
    const edge: Edge = {
      id: e.id,
      source: e.source,
      sourceHandle: e.sourceHandle,
      target: e.target,
      targetHandle: e.targetHandle,
      animated,
    };
    return edge;
  });
  const unchanged = next.length === previous.length && next.every((e, i) => e === previous[i]);
  return unchanged ? (previous as Edge[]) : next;
}


function CanvasSurface({ projectName }: { projectName: string }) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const agentOpen = useClaudeStore((s) => s.openProject === projectName);
  // 新建分组：先选这个分组对应哪种项目条目，再填名称和描述。
  const [newGroupMenu, setNewGroupMenu] = useState(false);
  const [addingGroup, setAddingGroup] = useState<"character" | "scene" | "prop" | "product" | null>(null);
  const [pickingCharacter, setPickingCharacter] = useState(false);
  const [flowOpen, setFlowOpen] = useState(false);
  const [markdown, setMarkdown] = useState<{ file: string; title: string } | null>(null);
  // 起始帧联合图 / 单元视频 / 配音走流水线任务：记下 任务 id → 节点 id，任务在跑时节点显示「运行中」，跑完重读画布。
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const trackedRows = useTaskRowsByIds(Object.keys(tracked));
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const { graph, error, reload } = useCanvasGraph(projectName, agentOpen);
  const activeTaskIds = useMemo(
    () =>
      Object.keys(tracked).filter((taskId) => {
        const row = trackedRows.get(taskId);
        return !row || isActiveStatus(row.status);
      }),
    [tracked, trackedRows],
  );
  const busyNodeIds = useMemo(() => new Set(activeTaskIds.map((taskId) => tracked[taskId] ?? "")), [activeTaskIds, tracked]);
  const previousActive = useRef(0);
  useEffect(() => {
    if (activeTaskIds.length < previousActive.current) void reload();
    previousActive.current = activeTaskIds.length;
  }, [activeTaskIds, reload]);
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { screenToFlowPosition, getNodes, getInternalNode, getNodesBounds, setViewport } = useReactFlow<CanvasFlowNode, Edge>();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  /**
   * 把视图摆到指定节点上（缺省全部）：缩放取「刚好放下」与下限之间，横向放得下就居中；纵向始终顶端对齐
   * 工具栏下沿——内容比窗口高时从头看起，而不是被居中后顶部藏在工具栏后面。
   */
  const frame = useCallback(
    (ids: readonly string[] | null, maxZoom: number, duration = 0) => {
      const rect = surfaceRef.current?.getBoundingClientRect();
      const targets = getNodes().filter((n) => !ids || ids.includes(n.id));
      if (!rect || targets.length === 0) return;
      const bounds = getNodesBounds(targets);
      const availableWidth = rect.width - FRAME_PAD.left - FRAME_PAD.right;
      const availableHeight = rect.height - FRAME_PAD.top - FRAME_PAD.bottom;
      const fit = Math.min(availableWidth / Math.max(bounds.width, 1), availableHeight / Math.max(bounds.height, 1));
      const zoom = Math.min(maxZoom, Math.max(FRAME_MIN_ZOOM, fit));
      const slack = Math.max(0, availableWidth - bounds.width * zoom);
      void setViewport(
        { x: FRAME_PAD.left + slack / 2 - bounds.x * zoom, y: FRAME_PAD.top - bounds.y * zoom, zoom },
        { duration },
      );
    },
    [getNodes, getNodesBounds, setViewport],
  );
  const fitted = useRef(false);
  const signatures = useRef(new Map<string, string>());
  const graphRef = useRef(graph);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [branch, setBranch] = useState<{ steps: CanvasBranchStep[]; current: number | null } | null>(null);
  const [imageModels, setImageModels] = useState<ImageModelChoices>({ options: [], providerNames: {}, modelNames: {} });
  useEffect(() => {
    graphRef.current = graph;
  }, [graph]);

  useEffect(() => {
    const controller = new AbortController();
    API.getModelCandidates({ signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setImageModels({ options: res.image.default, providerNames: res.provider_names, modelNames: res.model_names });
      })
      .catch(() => {
        // 候选拿不到时模型下拉为空，节点仍按项目默认运行。
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!graph) return;
    setNodes((prev) => toFlowNodes(graph, prev, signatures.current));
    setEdges((prev) => toFlowEdges(graph, prev));
    if (!fitted.current && graph.nodes.length > 0) {
      fitted.current = true;
      // 首次打开从第一个分组看起（放得下就按它取景），而不是把所有内容缩成看不清的一整张。
      const first = graph.nodes.find((n) => n.type === "group");
      const ids = first ? [first.id, ...graph.nodes.filter((n) => n.parentId === first.id).map((n) => n.id)] : null;
      requestAnimationFrame(() => requestAnimationFrame(() => frame(ids, 1)));
    }
  }, [graph, setNodes, setEdges, frame]);


  const guarded = useCallback(
    async (action: () => Promise<unknown>, failureKey: "node_canvas_action_failed" | "node_canvas_run_failed" = "node_canvas_action_failed") => {
      try {
        await action();
      } catch (err) {
        pushToast(t(failureKey, { message: errMsg(err) }), "error");
      } finally {
        await reload();
      }
    },
    [pushToast, reload, t],
  );

  useEffect(() => {
    useCanvasContextStore.getState().register(projectName, {
      nodeIds: () => graphRef.current?.nodes.map((n) => n.id) ?? [],
      compose: (text, contextIds) => {
        const nodes = graphRef.current?.nodes ?? [];
        return buildAgentNote(text, referencedNodeIds(text, contextIds, nodes), nodes);
      },
    });
    return () => useCanvasContextStore.getState().register(projectName, null);
  }, [projectName]);

  /** React Flow 量到的节点大小：重排按它来，节点变高 / 变宽之后不会互相压住。 */
  const measuredSizes = useCallback((): NodeSizes => {
    const sizes = new Map<string, { width: number; height: number }>();
    for (const n of getNodes()) {
      if (n.measured?.width && n.measured.height) sizes.set(n.id, { width: n.measured.width, height: n.measured.height });
    }
    return sizes;
  }, [getNodes]);

  /** 重排一个分组：集分组按矩阵排，其余分组按连线方向排。返回组内相对位置与分组需要的大小。 */
  const tidyOneGroup = useCallback(
    async (groupId: string) => {
      const stored = graphRef.current?.nodes ?? [];
      const members = stored.filter((n) => n.parentId === groupId);
      if (members.length === 0) return null;
      const sizes = measuredSizes();
      if (stored.find((n) => n.id === groupId)?.data.episode != null) return tidyEpisodeGroup(members, sizes);
      const ids = new Set(members.map((m) => m.id));
      const children = members.map((m) => ({ id: m.id, type: m.type, width: sizes.get(m.id)?.width ?? 280, height: sizes.get(m.id)?.height ?? 160 }));
      return layoutGroup(children, (graphRef.current?.edges ?? []).filter((e) => ids.has(e.source) && ids.has(e.target)));
    },
    [measuredSizes],
  );

  const actions = useMemo<CanvasActions>(
    () => ({
      projectName,
      setText: (nodeId, text) => guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { text } })),
      setNote: (nodeId, note) => guarded(() => API.setCanvasNodeNote(projectName, nodeId, note)),
      setAspectRatio: (nodeId, aspectRatio) =>
        guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { aspect_ratio: aspectRatio } })),
      setData: (nodeId, data) => guarded(() => API.updateCanvasNode(projectName, nodeId, { data })),
      setModel: (nodeId, model) => guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { model: model || null } })),
      imageModels,
      uploadImage: (nodeId, file) => guarded(() => API.fillCanvasImage(projectName, nodeId, file)),
      resizeGroup: (nodeId, position, width, height) =>
        guarded(() => API.updateCanvasNode(projectName, nodeId, { position, data: { width, height } })),
      tidyGroup: (groupId) =>
        guarded(async () => {
          const result = await tidyOneGroup(groupId);
          if (!result) return;
          await API.updateCanvasLayout(projectName, result.positions);
          await API.updateCanvasNode(projectName, groupId, { data: { width: result.width, height: result.height } });
        }),
      addToContext: (nodeId) => {
        useCanvasContextStore.getState().add(projectName, nodeId);
        useClaudeStore.getState().open(projectName);
      },
      saveUnitField: (episode, unitId, field, value) =>
        guarded(async () => {
          await API.saveCanvasUnitField(projectName, episode, unitId, field, value);
          pushToast(t("node_canvas_saved_to_script"), "success");
        }),
      openMarkdown: (file, title) => setMarkdown({ file, title }),
      run: async (nodeId) => {
        // 流水线节点（起始帧联合图 / 单元视频 / 配音）：走已有的生成链路，任务跟踪到节点上。
        const data = graphRef.current?.nodes.find((n) => n.id === nodeId)?.data;
        if (data && typeof data.ep === "number" && data.step) {
          const track = (taskIds: string[]) =>
            setTracked((prev) => ({ ...prev, ...Object.fromEntries(taskIds.map((taskId) => [taskId, nodeId])) }));
          const episode = data.ep;
          if (data.step === "kf_gen" && data.sheet) {
            await guarded(async () => track((await enqueueKeyframeSheet(projectName, episode, data.sheet ?? "")).taskIds), "node_canvas_run_failed");
            return;
          }
          if (data.step === "video_gen" && data.unit) {
            await guarded(async () => track((await enqueueReferenceVideoUnit(projectName, episode, data.unit ?? "")).taskIds), "node_canvas_run_failed");
            return;
          }
          if (data.step === "voice_gen" && data.unit) {
            await guarded(async () => track((await enqueueAutoDub(projectName, episode, { unit_ids: [data.unit ?? ""] })).taskIds), "node_canvas_run_failed");
            return;
          }
        }
        // 上游还有没产出或已过期的节点时，先让用户确认整条链再按顺序跑。
        try {
          // 整链运行目前只针对图片供应商节点（视频 / 语音节点的上游按需手动先跑）。
          if (graphRef.current?.nodes.find((n) => n.id === nodeId)?.type !== "image_generate") throw new Error("single");
          const plan = await API.getCanvasBranch(projectName, nodeId);
          if (plan.nodes.length > 1) {
            setBranch({ steps: plan.nodes, current: null });
            return;
          }
        } catch {
          // 取不到计划就按单个节点运行，运行本身会报出具体原因。
        }
        await guarded(async () => {
          await API.runCanvasNode(projectName, nodeId);
          pushToast(t("node_canvas_run_started"), "success");
        }, "node_canvas_run_failed");
      },
      remove: (nodeId) => guarded(() => API.deleteCanvasNode(projectName, nodeId)),
    }),
    [guarded, imageModels, projectName, pushToast, t, tidyOneGroup],
  );

  const executeBranch = useCallback(async () => {
    if (!branch) return;
    try {
      await runBranch(branch.steps, {
        onStep: (_step, index) => setBranch((prev) => (prev ? { ...prev, current: index } : prev)),
        getTask: (taskId) => API.getTask(taskId),
        runStep: async (step) => {
          return (await API.runCanvasNode(projectName, step.id)).task_ids;
        },
      });
      pushToast(t("node_canvas_branch_done"), "success");
    } catch (err) {
      const failed = err instanceof BranchStepFailed ? ` (${err.step.id})` : "";
      pushToast(t("node_canvas_run_failed", { message: `${errMsg(err)}${failed}` }), "error");
    } finally {
      setBranch(null);
      await reload();
    }
  }, [branch, projectName, pushToast, reload, t]);

  const [selectMode, setSelectMode] = useState(false);
  const groupableIds = useMemo(() => {
    const byId = new Map((graph?.nodes ?? []).map((n) => [n.id, n]));
    return selectedIds.filter((id) => {
      const node = byId.get(id);
      return node && node.type !== "group" && !node.parentId;
    });
  }, [graph, selectedIds]);

  const groupSelected = useCallback(() => {
    const picked = getNodes().filter((n) => groupableIds.includes(n.id));
    if (picked.length < 2) return;
    // 分组框按节点的实际尺寸（没渲染出来就用默认值）外扩一圈，顶上留出标题的位置。
    const box = picked.map((n) => ({
      x: n.position.x,
      y: n.position.y,
      w: n.measured?.width ?? n.width ?? 280,
      h: n.measured?.height ?? n.height ?? 200,
    }));
    const left = Math.min(...box.map((b) => b.x)) - GROUP_PADDING;
    const top = Math.min(...box.map((b) => b.y)) - GROUP_PADDING - GROUP_HEADER;
    const right = Math.max(...box.map((b) => b.x + b.w)) + GROUP_PADDING;
    const bottom = Math.max(...box.map((b) => b.y + b.h)) + GROUP_PADDING;
    void guarded(() =>
      API.groupCanvasNodes(projectName, {
        node_ids: picked.map((n) => n.id),
        label: t("node_canvas_group_default"),
        position: { x: left, y: top },
        width: right - left,
        height: bottom - top,
      }),
    );
  }, [getNodes, groupableIds, guarded, projectName, t]);

  // Cmd/Ctrl+G：把选中的节点收进一个分组。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(input|textarea|select)$/i.test(target.tagName)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "g" && groupableIds.length >= 2) {
        event.preventDefault();
        groupSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [groupSelected, groupableIds.length]);

  // 刚放进画布的分组 / 刚补进新节点的集分组：等组内节点渲染出实际尺寸（图片加载完后节点才定高，所以要尺寸稳定一小会儿），
  // 排一次版（集分组按矩阵，其余用 ELK）并存下位置，之后位置归用户。
  const laidOut = useRef(new Set<string>());
  useEffect(() => {
    if (!graph) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const group of graph.nodes) {
      // 标记清掉之后允许下一批新节点再触发一次（Agent 往已有的集分组里补单元时）。
      if (group.type === "group" && !group.data.autolayout) laidOut.current.delete(group.id);
      if (group.type !== "group" || !group.data.autolayout || laidOut.current.has(group.id)) continue;
      const kids = nodes.filter((n) => n.parentId === group.id);
      if (kids.length === 0 || kids.some((k) => !k.measured?.width || !k.measured.height)) continue;
      const sizes = new Map(kids.map((k) => [k.id, { width: k.measured?.width ?? 0, height: k.measured?.height ?? 0 }]));
      const stored = graph.nodes.filter((n) => n.parentId === group.id);
      const children = kids.map((k) => ({ id: k.id, type: k.type ?? "text", width: k.measured?.width ?? 0, height: k.measured?.height ?? 0 }));
      const ids = new Set(kids.map((k) => k.id));
      const edgesInside = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
      const isEpisode = group.data.episode != null;
      timers.push(
        setTimeout(() => {
          laidOut.current.add(group.id);
          // 集分组按矩阵排（行高取最高的节点）；其余分组按连线方向排。
          (isEpisode ? Promise.resolve(tidyEpisodeGroup(stored, sizes)) : layoutGroup(children, edgesInside))
            .then(async (result) => {
              await API.updateCanvasLayout(projectName, result.positions);
              await API.updateCanvasNode(projectName, group.id, {
                data: { autolayout: false, width: result.width, height: result.height },
              });
              await reload();
            })
            .catch(() => laidOut.current.delete(group.id));
        }, AUTOLAYOUT_SETTLE_MS),
      );
    }
    return () => timers.forEach(clearTimeout);
  }, [graph, nodes, projectName, reload]);

  /** 拖完一个落盘节点：中心落在某个分组框里就归入它，拖出所属分组框就放出来；其余只存位置。 */
  const onDragStop = useCallback(
    (dragged: CanvasFlowNode[]) => {
      const byId = new Map((graphRef.current?.nodes ?? []).map((n) => [n.id, n]));
      const groups = getNodes()
        .filter((n) => n.type === "group")
        .flatMap((g) => {
          const abs = getInternalNode(g.id)?.internals.positionAbsolute;
          const width = Number(g.style?.width ?? g.measured?.width ?? 0);
          const height = Number(g.style?.height ?? g.measured?.height ?? 0);
          return abs && width > 0 && height > 0 ? [{ id: g.id, abs, width, height }] : [];
        })
        .sort((a, b) => a.width * a.height - b.width * b.height);

      const positions: Record<string, { x: number; y: number }> = {};
      const moves = new Map<string | null, { ids: string[]; positions: Record<string, { x: number; y: number }> }>();
      for (const node of dragged) {
        const stored = byId.get(node.id);
        const abs = getInternalNode(node.id)?.internals.positionAbsolute;
        if (!stored || node.type === "group" || !abs) {
          positions[node.id] = { x: node.position.x, y: node.position.y };
          continue;
        }
        const cx = abs.x + (node.measured?.width ?? 240) / 2;
        const cy = abs.y + (node.measured?.height ?? 160) / 2;
        const target = groups.find((g) => g.id !== node.id && cx >= g.abs.x && cx <= g.abs.x + g.width && cy >= g.abs.y && cy <= g.abs.y + g.height);
        const targetId = target?.id ?? null;
        if (targetId === (node.parentId ?? null)) {
          positions[node.id] = { x: node.position.x, y: node.position.y };
          continue;
        }
        const move = moves.get(targetId) ?? { ids: [], positions: {} };
        move.ids.push(node.id);
        move.positions[node.id] = target ? { x: abs.x - target.abs.x, y: abs.y - target.abs.y } : { x: abs.x, y: abs.y };
        moves.set(targetId, move);
      }
      void guarded(async () => {
        for (const [groupId, move] of moves) {
          await API.setCanvasMembers(projectName, { node_ids: move.ids, group_id: groupId, positions: move.positions });
        }
        if (Object.keys(positions).length > 0) await API.updateCanvasLayout(projectName, positions);
      });
    },
    [getInternalNode, getNodes, guarded, projectName],
  );

  // 删分组框只是拆组：React Flow 默认会把子节点一并删掉，这里把没被单独选中的子节点留下。
  const onBeforeDelete = useCallback<OnBeforeDelete<CanvasFlowNode, Edge>>(({ nodes: doomed, edges: doomedEdges }) => {
    const groups = new Set(doomed.filter((n) => n.type === "group").map((n) => n.id));
    if (groups.size === 0) return Promise.resolve(true);
    const kept = doomed.filter((n) => !(n.parentId && groups.has(n.parentId) && !n.selected));
    const spared = new Set(doomed.filter((n) => !kept.includes(n)).map((n) => n.id));
    return Promise.resolve({ nodes: kept, edges: doomedEdges.filter((e) => !spared.has(e.source) && !spared.has(e.target)) });
  }, []);

  /** 一个按钮整理全部：每个分组先按里面节点的实际大小重排，再把画布上的分组与散节点按数据流排开。 */
  const tidy = useCallback(() => {
    if (!graph) return;
    void guarded(async () => {
      const sizes = new Map(measuredSizes());
      const positions: Record<string, { x: number; y: number }> = {};
      const resized: Array<{ id: string; width: number; height: number }> = [];
      for (const group of graph.nodes.filter((n) => n.type === "group")) {
        const result = await tidyOneGroup(group.id);
        if (!result) continue;
        Object.assign(positions, result.positions);
        resized.push({ id: group.id, width: result.width, height: result.height });
        sizes.set(group.id, { width: result.width, height: result.height });
      }
      Object.assign(positions, tidyLayout(graph.nodes, graph.edges, sizes));
      if (Object.keys(positions).length === 0) return;
      await API.updateCanvasLayout(projectName, positions);
      await Promise.all(resized.map((g) => API.updateCanvasNode(projectName, g.id, { data: { width: g.width, height: g.height } })));
    });
  }, [graph, guarded, measuredSizes, projectName, tidyOneGroup]);

  // 通知 / 任务失败提示点进来的「定位到某个角色」：把画布平移缩放到该角色节点并选中它。
  useEffect(() => {
    if (!scrollTarget || scrollTarget.type !== "character") return;
    const nodeId = nodes.find((n) => n.type === "group" && n.data.asset === "character" && n.data.label === scrollTarget.id)?.id;
    if (!nodeId) {
      if (Date.now() >= scrollTarget.expires_at) clearScrollTarget(scrollTarget.request_id);
      return;
    }
    setNodes((prev) => prev.map((n) => (n.selected === (n.id === nodeId) ? n : { ...n, selected: n.id === nodeId })));
    frame([nodeId], 1, 400);
    clearScrollTarget(scrollTarget.request_id);
  }, [scrollTarget, nodes, frame, setNodes, clearScrollTarget]);

  // 侧栏「角色」入口：/canvas?focus=characters，把视图移到角色节点上。
  const search = useSearch();
  const handledFocus = useRef("");
  useEffect(() => {
    if (new URLSearchParams(search).get("focus") !== "characters" || handledFocus.current === search) return;
    const characterNodes = nodes.filter((n) => n.type === "group" && n.data.asset === "character");
    if (characterNodes.length === 0) return;
    handledFocus.current = search;
    requestAnimationFrame(() => frame(characterNodes.map((n) => n.id), 0.9, 400));
  }, [search, nodes, frame]);

  const importCharacters = useCallback(
    async (ids: string[]) => {
      try {
        await API.applyAssetsToProject({ asset_ids: ids, target_project: projectName, conflict_policy: "skip" });
        pushToast(t("assets:import_count", { count: ids.length }), "success");
        await API.importCanvasAssets(projectName);
        await reload();
      } catch (err) {
        pushToast(errMsg(err), "error");
      } finally {
        setPickingCharacter(false);
      }
    },
    [projectName, pushToast, reload, t],
  );

  const viewportCenter = useCallback(() => {
    const rect = surfaceRef.current?.getBoundingClientRect();
    const jitter = (Math.random() - 0.5) * 2 * NEW_NODE_JITTER_PX;
    return screenToFlowPosition({
      x: (rect?.left ?? 0) + (rect?.width ?? 0) / 2 + jitter,
      y: (rect?.top ?? 0) + (rect?.height ?? 0) / 2 + jitter,
    });
  }, [screenToFlowPosition]);

  const addNode = useCallback(
    (type: CanvasNodeType, data?: CanvasNodeData) =>
      guarded(() => API.createCanvasNode(projectName, { type, position: viewportCenter(), data })),
    [guarded, projectName, viewportCenter],
  );

  const onFilePicked = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      void guarded(() => API.uploadCanvasImage(projectName, file, viewportCenter()));
    },
    [guarded, projectName, viewportCenter],
  );

  const lineage = useMemo(() => (focusId && graph ? lineageOf(focusId, graph.edges, graph.nodes) : null), [focusId, graph]);
  // 框选时分组框要「透明」：它几乎盖满画布，不放行的话从组内起拖会把整个分组拖走而不是画选框。
  const selectionKeyHeld = useKeyPress(SELECTION_KEYS);
  const selecting = selectMode || selectionKeyHeld;
  // 拖动时 RF 只替换被拖节点的对象：淡化后的副本按原对象缓存，其余节点引用不变、不会跟着重绘。
  const dimCache = useMemo(() => (lineage ? new WeakMap<CanvasFlowNode, CanvasFlowNode>() : null), [lineage]);
  const shownNodes = useMemo(() => {
    const dim = (n: CanvasFlowNode): CanvasFlowNode => {
      let copy = dimCache?.get(n);
      if (!copy) {
        copy = { ...n, style: { ...n.style, opacity: DIMMED_OPACITY } };
        dimCache?.set(n, copy);
      }
      return copy;
    };
    const base = lineage ? nodes.map((n) => (lineage.nodeIds.has(n.id) ? n : dim(n))) : nodes;
    return selecting
      ? base.map((n) => (GROUP_NODE_TYPES.has(n.type ?? "") ? { ...n, selectable: false, style: { ...n.style, pointerEvents: "none" as const } } : n))
      : base;
  }, [lineage, dimCache, nodes, selecting]);
  const shownEdges = useMemo(
    () =>
      lineage
        ? edges.map((e) => (lineage.edgeIds.has(e.id) ? { ...e, style: { ...e.style, stroke: "var(--color-accent)", strokeWidth: 2 } } : { ...e, style: { ...e.style, opacity: DIMMED_OPACITY / 2 } }))
        : edges,
    [lineage, edges],
  );

  const graphNodes = graph?.nodes;
  const isValidConnection = useCallback(
    (c: Connection | Edge) =>
      isValidCanvasConnection(graphNodes ?? [], {
        source: c.source,
        sourceHandle: c.sourceHandle ?? null,
        target: c.target,
        targetHandle: c.targetHandle ?? null,
      }),
    [graphNodes],
  );

  if (error && !graph) {
    return <div className="p-8 text-sm text-red-300">{t("node_canvas_load_failed")}: {errMsg(error)}</div>;
  }
  if (!graph) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-text-3">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t("node_canvas_loading")}
      </div>
    );
  }

  return (
    <CanvasActionsContext.Provider value={actions}>
      <CanvasBusyContext.Provider value={busyNodeIds}>
      <div ref={surfaceRef} className="relative h-full w-full" data-testid="node-canvas">
        <ReactFlow<CanvasFlowNode, Edge>
          nodes={shownNodes}
          edges={shownEdges}
          nodeTypes={canvasNodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          isValidConnection={isValidConnection}
          onConnect={(c) =>
            void guarded(() =>
              API.createCanvasEdge(projectName, {
                source: c.source,
                source_handle: c.sourceHandle ?? "",
                target: c.target,
                target_handle: c.targetHandle ?? "",
              }),
            )
          }
          onSelectionChange={({ nodes: picked }) => {
            setFocusId(picked.length === 1 ? picked[0].id : null);
            setSelectedIds(picked.map((n) => n.id));
          }}
          onNodeDragStop={(_, __, dragged) => onDragStop(dragged)}
          onDelete={({ nodes: removedNodes, edges: removedEdges }) => {
            // 删节点时服务端会连带清掉它的连线，只单独删与已删节点无关的连线。
            const removedIds = new Set(removedNodes.map((n) => n.id));
            const lone = removedEdges.filter((e) => !removedIds.has(e.source) && !removedIds.has(e.target));
            void guarded(() =>
              Promise.all([
                ...removedNodes.map((n) => API.deleteCanvasNode(projectName, n.id)),
                ...lone.map((e) => API.deleteCanvasEdge(projectName, e.id)),
              ]),
            );
          }}
          // 默认只认 Backspace（Mac 的 Delete 键）；Windows / Linux 键盘的 Delete 也要能删。
          deleteKeyCode={DELETE_KEYS}
          onBeforeDelete={onBeforeDelete}
          selectionKeyCode={SELECTION_KEYS}
          selectionMode={SelectionMode.Partial}
          selectionOnDrag={selectMode}
          panOnDrag={selectMode ? PAN_BUTTONS : true}
          onlyRenderVisibleElements
          defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          colorMode="dark"
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          <Controls showInteractive={false} onFitView={() => frame(null, 1, 300)} />
          <MiniMap pannable zoomable position="bottom-right" style={MINIMAP_STYLE} className="!hidden !bg-bg-grad-a/80 lg:!block" />
        </ReactFlow>

        <div className="pointer-events-none absolute left-4 top-4 flex flex-wrap items-center gap-2">
          <h1 className="pointer-events-auto mr-2 text-[14px] font-semibold text-text">{t("node_canvas_title")}</h1>
          <div className={CLUSTER_CLS}>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => void addNode("text")}>
            <Type className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_text")}
          </button>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => fileInput.current?.click()}>
            <ImagePlus className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_image")}
          </button>
          </div>
          <div className={CLUSTER_CLS}>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => void addNode("image_generate")}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_generate")}
          </button>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => void addNode("video_generate")}>
            <Video className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_video_generate")}
          </button>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => void addNode("audio_generate")}>
            <Mic className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_audio_generate")}
          </button>
          </div>
          <div className={CLUSTER_CLS}>
          <div className="relative">
            <button type="button" className={TOOL_BTN_CLS} aria-expanded={newGroupMenu} onClick={() => setNewGroupMenu((open) => !open)}>
              <UserPlus className="h-3.5 w-3.5" aria-hidden="true" />
              {t("node_canvas_new_group")}
            </button>
            {newGroupMenu && (
              <ul className="absolute left-0 top-full z-10 mt-1 w-48 rounded-[10px] border border-hairline bg-bg-grad-a/95 p-1 shadow-xl backdrop-blur">
                {(["character", "scene", "prop", "product"] as const).map((kind) => (
                  <li key={kind}>
                    <button
                      type="button"
                      className="w-full rounded-[6px] px-2 py-1.5 text-left text-[12px] text-text-2 hover:bg-white/5"
                      onClick={() => {
                        setNewGroupMenu(false);
                        setAddingGroup(kind);
                      }}
                    >
                      {t(`node_canvas_group_kind_${kind}`)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button
            type="button"
            className={TOOL_BTN_CLS}
            title={t("node_canvas_import_assets_hint")}
            onClick={() => void guarded(async () => {
              const res = await API.importCanvasAssets(projectName);
              pushToast(t("node_canvas_import_assets_done", { count: res.imported }), "success");
            })}
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_import_assets")}
          </button>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => setPickingCharacter(true)}>
            <Library className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_import_characters")}
          </button>
          </div>
          <div className={CLUSTER_CLS}>
          <button type="button" className={TOOL_BTN_CLS} onClick={() => setFlowOpen(true)}>
            <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_flow_button")}
          </button>
          <button
            type="button"
            className={`${TOOL_BTN_CLS} ${selectMode ? "!bg-accent/20 !text-text" : ""}`}
            aria-pressed={selectMode}
            title={t("node_canvas_select_mode_hint")}
            onClick={() => setSelectMode((on) => !on)}
          >
            <BoxSelect className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_select_mode")}
          </button>
          <button type="button" className={TOOL_BTN_CLS} onClick={tidy}>
            <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_tidy")}
          </button>
          <div className="pointer-events-auto relative">
            <button type="button" className={TOOL_BTN_CLS} aria-expanded={templatesOpen} onClick={() => setTemplatesOpen((open) => !open)}>
              <Workflow className="h-3.5 w-3.5" aria-hidden="true" />
              {t("node_canvas_templates_button", { count: graph.templates.length })}
            </button>
            {templatesOpen && (
              <div className="absolute left-0 top-full z-10 mt-1 w-72 rounded-[10px] border border-hairline bg-bg-grad-a/95 p-2 shadow-xl backdrop-blur">
                <ul className="mb-2 max-h-56 overflow-y-auto">
                  {graph.templates.length === 0 && <li className="px-2 py-1.5 text-[12px] text-text-4">{t("node_canvas_templates_empty")}</li>}
                  {graph.templates.map((tpl) => (
                    <li key={tpl.id} className="flex items-center gap-1">
                      <button
                        type="button"
                        className="flex-1 truncate rounded-[6px] px-2 py-1.5 text-left text-[12px] text-text-2 hover:bg-white/5"
                        onClick={() => {
                          setTemplatesOpen(false);
                          void guarded(() => API.instantiateCanvasTemplate(projectName, tpl.id, viewportCenter()));
                        }}
                      >
                        {tpl.name} <span className="text-text-4">· {t("node_canvas_template_nodes", { count: tpl.node_count })}</span>
                      </button>
                      <button
                        type="button"
                        className="rounded-[6px] p-1.5 text-text-4 hover:bg-white/5 hover:text-text"
                        aria-label={t("node_canvas_template_delete")}
                        onClick={() => void guarded(() => API.deleteCanvasTemplate(projectName, tpl.id))}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
                <form
                  className="flex items-center gap-1 border-t border-hairline pt-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const name = templateName.trim();
                    if (!name || selectedIds.length === 0) return;
                    void guarded(() => API.saveCanvasTemplate(projectName, name, selectedIds)).then(() => setTemplateName(""));
                  }}
                >
                  <input
                    className="min-w-0 flex-1 rounded-[6px] border border-hairline bg-transparent px-2 py-1 text-[12px] text-text placeholder:text-text-4 focus-visible:outline-none"
                    value={templateName}
                    placeholder={t("node_canvas_template_name_placeholder")}
                    aria-label={t("node_canvas_template_name_placeholder")}
                    onChange={(e) => setTemplateName(e.target.value)}
                  />
                  <button type="submit" className={GHOST_BTN_CLS} disabled={selectedIds.length === 0 || templateName.trim() === ""} title={t("node_canvas_template_save_hint")}>
                    <Save className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("node_canvas_template_save")}
                  </button>
                </form>
              </div>
            )}
          </div>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              onFilePicked(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>

        {groupableIds.length >= 2 && (
          <div
            role="toolbar"
            aria-label={t("node_canvas_selection_bar")}
            className="absolute inset-x-0 bottom-4 z-10 mx-auto flex w-fit items-center gap-1 rounded-[12px] border border-hairline bg-bg-grad-a/95 p-1.5 pl-3 shadow-xl backdrop-blur"
          >
            <span className="mr-2 text-[12px] text-text-2">{t("node_canvas_selection_count", { count: groupableIds.length })}</span>
            <button type="button" className={ACCENT_BTN_SM_CLS} onClick={groupSelected}>
              <Group className="h-3.5 w-3.5" aria-hidden="true" />
              {t("node_canvas_group_selected")}
            </button>
          </div>
        )}

        {flowOpen && (
          <FlowModal
            projectName={projectName}
            onClose={() => setFlowOpen(false)}
            onJump={(nodeId) => {
              setFlowOpen(false);
              setNodes((prev) => prev.map((n) => (n.selected === (n.id === nodeId) ? n : { ...n, selected: n.id === nodeId })));
              requestAnimationFrame(() => frame([nodeId], 1, 400));
            }}
            onImported={reload}
          />
        )}

        {markdown && <MarkdownModal projectName={projectName} file={markdown.file} title={markdown.title} onClose={() => setMarkdown(null)} />}

        {addingGroup && (
          <AssetFormModal
            type={addingGroup === "product" ? "prop" : addingGroup}
            typeLabel={addingGroup === "product" ? t("node_canvas_group_kind_product") : undefined}
            mode="create"
            onClose={() => setAddingGroup(null)}
            onSubmit={async ({ name, description }) => {
              await API.createCanvasAssetGroup(projectName, { kind: addingGroup, name, description, position: viewportCenter() });
              await reload();
              setAddingGroup(null);
            }}
          />
        )}

        {pickingCharacter && (
          <AssetPickerModal
            type="character"
            existingNames={new Set(graph.nodes.filter((n) => n.type === "group" && n.data.asset === "character").map((n) => n.data.label ?? ""))}
            onClose={() => setPickingCharacter(false)}
            onImport={(ids) => {
              void importCharacters(ids);
            }}
          />
        )}

        {branch && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label={t("node_canvas_branch_title")}>
            <div className="w-full max-w-md rounded-[14px] border border-hairline bg-bg-grad-a p-4 shadow-2xl">
              <h2 className="mb-1 text-[14px] font-semibold text-text">{t("node_canvas_branch_title")}</h2>
              <p className="mb-3 text-[12px] text-text-3">{t("node_canvas_branch_hint", { count: branch.steps.length })}</p>
              <ol className="mb-4 space-y-1.5">
                {branch.steps.map((step, index) => (
                  <li key={step.id} className="flex items-center gap-2 text-[12px] text-text-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/5 text-[10px]">
                      {branch.current === index ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> : index + 1}
                    </span>
                    <span className="truncate">{step.id}</span>
                    <span className="ml-auto text-[10.5px] text-text-4">{t(`node_canvas_branch_reason_${step.reason}`)}</span>
                  </li>
                ))}
              </ol>
              <div className="flex justify-end gap-2">
                <button type="button" className={GHOST_BTN_CLS} disabled={branch.current !== null} onClick={() => setBranch(null)}>
                  {t("node_canvas_branch_cancel")}
                </button>
                <button type="button" className={ACCENT_BTN_SM_CLS} disabled={branch.current !== null} onClick={() => void executeBranch()}>
                  {t("node_canvas_branch_run")}
                </button>
              </div>
            </div>
          </div>
        )}

        {graph.nodes.length === 0 && (
          <p className="pointer-events-none absolute inset-x-0 top-1/2 mx-auto max-w-md -translate-y-1/2 text-center text-[13px] text-text-4">
            {t("node_canvas_empty")}
          </p>
        )}
      </div>
      </CanvasBusyContext.Provider>
    </CanvasActionsContext.Provider>
  );
}

export function NodeCanvasPage({ projectName }: { projectName: string }) {
  return (
    <ReactFlowProvider>
      <CanvasSurface projectName={projectName} />
    </ReactFlowProvider>
  );
}
