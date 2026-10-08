import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bot, Check, ImagePlus, Layers, Loader2, PenLine, Sparkles, Type } from "lucide-react";
import { API } from "@/api";
import { enqueueKeyframeSheet } from "@/actions/generation";
import { isActiveStatus, useTaskRowsByIds } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useClaudeStore } from "@/stores/claude-store";
import { errMsg } from "@/utils/async";
import type { CanvasGraph, CanvasNodeData, CanvasNodeType } from "@/types/canvas";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { CanvasActionsContext, type CanvasActions } from "./canvas-context";
import { isValidCanvasConnection, lineageOf } from "./canvas-rules";
import { canvasNodeTypes, type CanvasFlowNode } from "./CanvasNodes";
import { useCanvasGraph } from "./use-canvas-graph";

const NEW_NODE_JITTER_PX = 24;

const DIMMED_OPACITY = 0.28;

function toFlowNodes(graph: CanvasGraph, previous: readonly CanvasFlowNode[]): CanvasFlowNode[] {
  const local = new Map(previous.map((n) => [n.id, n]));
  return graph.nodes.map((node) => {
    const existing = local.get(node.id);
    const derived = node.derived === true;
    const group = node.type === "episode";
    return {
      id: node.id,
      type: node.type,
      // 拖动中的位置属于本地：服务端只在拖动结束后才会收到新位置。
      position: existing?.dragging ? existing.position : node.position,
      data: node.data,
      selected: existing?.selected ?? false,
      dragging: existing?.dragging ?? false,
      ...(node.parentId ? { parentId: node.parentId } : {}),
      ...(group ? { style: { width: node.width, height: node.height }, zIndex: -1 } : {}),
      // 项目推导的节点只读：不能删，也不能单独拖离集分组（拖动整个分组）。
      draggable: !derived || group,
      deletable: !derived,
    };
  });
}

function toFlowEdges(graph: CanvasGraph): Edge[] {
  return graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    sourceHandle: e.sourceHandle,
    target: e.target,
    targetHandle: e.targetHandle,
    deletable: !e.virtual,
    animated: graph.nodes.find((n) => n.id === e.target)?.data.status === "running",
    ...(e.virtual ? { style: { strokeDasharray: "4 4", opacity: 0.6 } } : {}),
  }));
}

/** 派生单元节点 id `p:unit:<集>:<单元>` → 集号与单元 id。 */
function parseUnitNodeId(id: string): { episode: number; unitId: string } | null {
  const [prefix, kind, episode, ...rest] = id.split(":");
  const number = Number(episode);
  return prefix === "p" && kind === "unit" && Number.isInteger(number) && rest.length > 0
    ? { episode: number, unitId: rest.join(":") }
    : null;
}

function CanvasSurface({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const agentOpen = useClaudeStore((s) => s.openProject === projectName);
  const agentPending = useClaudeStore((s) => s.pending[projectName] ?? false);
  const askAgent = useClaudeStore((s) => s.ask);
  const { graph, error, reload } = useCanvasGraph(projectName, agentOpen);
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { screenToFlowPosition, fitView } = useReactFlow<CanvasFlowNode, Edge>();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const fitted = useRef(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  // 已入队、正在跟踪的任务 → 它所属的节点键（H3 写作节点 id / `集:联合图`）。
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const trackedRows = useTaskRowsByIds(Object.keys(tracked));

  useEffect(() => {
    if (!graph) return;
    setNodes((prev) => toFlowNodes(graph, prev));
    setEdges(toFlowEdges(graph));
    if (!fitted.current && graph.nodes.length > 0) {
      fitted.current = true;
      requestAnimationFrame(() => void fitView({ padding: 0.3, maxZoom: 1 }));
    }
  }, [graph, setNodes, setEdges, fitView]);

  // 任务行还没进 store 时按进行中算；终态后节点恢复可点，并重新读一次画布拿到新产物。
  const activeTaskIds = useMemo(
    () => Object.keys(tracked).filter((taskId) => {
      const row = trackedRows.get(taskId);
      return row === undefined || isActiveStatus(row.status);
    }),
    [tracked, trackedRows],
  );
  const previousActive = useRef(0);
  useEffect(() => {
    if (activeTaskIds.length < previousActive.current) void reload();
    previousActive.current = activeTaskIds.length;
  }, [activeTaskIds, reload]);

  const busyNodeIds = useMemo(() => new Set(activeTaskIds.map((taskId) => tracked[taskId])), [activeTaskIds, tracked]);

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

  const actions = useMemo<CanvasActions>(
    () => ({
      projectName,
      setText: (nodeId, text) => guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { text } })),
      setAspectRatio: (nodeId, aspectRatio) =>
        guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { aspect_ratio: aspectRatio } })),
      run: (nodeId) =>
        guarded(async () => {
          await API.runCanvasNode(projectName, nodeId);
          pushToast(t("node_canvas_run_started"), "success");
        }, "node_canvas_run_failed"),
      remove: (nodeId) => guarded(() => API.deleteCanvasNode(projectName, nodeId)),
      runWriter: (nodeId) =>
        guarded(async () => {
          const byEpisode = new Map<number, string[]>();
          for (const edge of graph?.edges ?? []) {
            if (edge.target !== nodeId || edge.targetHandle !== "units") continue;
            const unit = parseUnitNodeId(edge.source);
            if (unit) byEpisode.set(unit.episode, [...(byEpisode.get(unit.episode) ?? []), unit.unitId]);
          }
          if (byEpisode.size === 0) throw new Error(t("node_canvas_h3_writer_no_units"));
          for (const [episode, unitIds] of byEpisode) {
            const res = await API.regenerateH3Prompts(projectName, episode, unitIds);
            setTracked((prev) => ({ ...prev, [res.task_id]: nodeId }));
          }
          pushToast(t("node_canvas_run_started"), "success");
        }, "node_canvas_run_failed"),
      runKeyframe: (episode, sheetId) =>
        guarded(async () => {
          const res = await enqueueKeyframeSheet(projectName, episode, sheetId);
          setTracked((prev) => ({ ...prev, ...Object.fromEntries(res.taskIds.map((id) => [id, `${episode}:${sheetId}`])) }));
        }, "node_canvas_run_failed"),
      busyNodeIds,
    }),
    [busyNodeIds, graph, guarded, projectName, pushToast, t],
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

  const lineage = useMemo(() => (focusId && graph ? lineageOf(focusId, graph.edges) : null), [focusId, graph]);
  const shownNodes = useMemo(
    () =>
      lineage
        ? nodes.map((n) => (lineage.nodeIds.has(n.id) ? n : { ...n, style: { ...n.style, opacity: DIMMED_OPACITY } }))
        : nodes,
    [lineage, nodes],
  );
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
          onSelectionChange={({ nodes: picked }) => setFocusId(picked.length === 1 ? picked[0].id : null)}
          onNodeDragStop={(_, __, dragged) =>
            void guarded(() =>
              API.updateCanvasLayout(
                projectName,
                Object.fromEntries(dragged.map((n) => [n.id, { x: n.position.x, y: n.position.y }])),
              ),
            )
          }
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
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          colorMode="dark"
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable className="!bg-bg-grad-a/80" />
        </ReactFlow>

        <div className="pointer-events-none absolute left-4 top-4 flex flex-wrap items-center gap-2">
          <h1 className="pointer-events-auto mr-2 text-[13px] font-semibold text-text-2">{t("node_canvas_title")}</h1>
          <button type="button" className={`pointer-events-auto ${GHOST_BTN_CLS}`} onClick={() => void addNode("text")}>
            <Type className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_text")}
          </button>
          <button type="button" className={`pointer-events-auto ${GHOST_BTN_CLS}`} onClick={() => fileInput.current?.click()}>
            <ImagePlus className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_image")}
          </button>
          <button type="button" className={`pointer-events-auto ${GHOST_BTN_CLS}`} onClick={() => void addNode("image_generate")}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_add_generate")}
          </button>
          {graph.episodes.length > 0 && (
            <>
              <button type="button" className={`pointer-events-auto ${GHOST_BTN_CLS}`} onClick={() => void addNode("h3_writer")}>
                <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
                {t("node_canvas_add_h3_writer")}
              </button>
              <div className="pointer-events-auto relative">
                <button type="button" className={GHOST_BTN_CLS} aria-expanded={pickerOpen} onClick={() => setPickerOpen((open) => !open)}>
                  <Layers className="h-3.5 w-3.5" aria-hidden="true" />
                  {t("node_canvas_episodes_button", { count: graph.view.episodes.length })}
                </button>
                {pickerOpen && (
                  <ul className="absolute left-0 top-full z-10 mt-1 max-h-72 w-64 overflow-y-auto rounded-[10px] border border-hairline bg-bg-grad-a/95 p-1 shadow-xl backdrop-blur">
                    {graph.episodes.map((ep) => {
                      const on = graph.view.episodes.includes(ep.episode);
                      return (
                        <li key={ep.episode}>
                          <button
                            type="button"
                            className="flex w-full items-center gap-2 rounded-[6px] px-2 py-1.5 text-left text-[12px] text-text-2 hover:bg-white/5"
                            onClick={() => {
                              const next = on
                                ? graph.view.episodes.filter((n) => n !== ep.episode)
                                : [...graph.view.episodes, ep.episode];
                              void guarded(() => API.setCanvasView(projectName, next));
                            }}
                          >
                            <span className="flex h-4 w-4 items-center justify-center">{on ? <Check className="h-3.5 w-3.5 text-accent" aria-hidden="true" /> : null}</span>
                            <span className="truncate">{t("node_canvas_episode_title", { episode: ep.episode })} {ep.title}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </>
          )}
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

        <form
          className="absolute inset-x-0 bottom-4 mx-auto flex w-[min(34rem,calc(100%-2rem))] items-center gap-2 rounded-[12px] border border-hairline bg-bg-grad-a/90 p-2 shadow-xl backdrop-blur"
          onSubmit={(e) => {
            e.preventDefault();
            void askAgent(projectName, {
              action: "canvas_assist",
              ...(focusId ? { resource_id: focusId } : {}),
              note: instruction.trim(),
            })
              .then(() => setInstruction(""))
              .catch((err: unknown) => pushToast(errMsg(err), "error"));
          }}
        >
          <Bot className="ml-1 h-4 w-4 shrink-0 text-text-3" aria-hidden="true" />
          <input
            className="min-w-0 flex-1 bg-transparent px-1 text-[13px] text-text placeholder:text-text-4 focus-visible:outline-none"
            value={instruction}
            placeholder={focusId ? t("node_canvas_agent_placeholder_selected") : t("node_canvas_agent_placeholder")}
            aria-label={t("node_canvas_agent_label")}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <button type="submit" className={GHOST_BTN_CLS} disabled={agentPending}>
            {agentPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
            {t("node_canvas_agent_send")}
          </button>
        </form>

        {graph.nodes.length === 0 && (
          <p className="pointer-events-none absolute inset-x-0 top-1/2 mx-auto max-w-md -translate-y-1/2 text-center text-[13px] text-text-4">
            {t("node_canvas_empty")}
          </p>
        )}
      </div>
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
