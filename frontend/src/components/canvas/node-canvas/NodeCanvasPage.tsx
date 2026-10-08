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
import { Bot, Check, ImagePlus, LayoutGrid, Layers, Loader2, PenLine, Save, Sparkles, Trash2, Type, Workflow } from "lucide-react";
import { API } from "@/api";
import { enqueueKeyframeSheet } from "@/actions/generation";
import { isActiveStatus, useTaskRowsByIds } from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useClaudeStore } from "@/stores/claude-store";
import { errMsg } from "@/utils/async";
import type { CanvasBranchStep, CanvasGraph, CanvasNodeData, CanvasNodeType } from "@/types/canvas";
import { ACCENT_BTN_SM_CLS, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { CanvasActionsContext, CanvasBusyContext, type CanvasActions, type ImageModelChoices } from "./canvas-context";
import { tidyLayout } from "./canvas-layout";
import { BranchStepFailed, runBranch } from "./branch-runner";
import { isValidCanvasConnection, lineageOf } from "./canvas-rules";
import { canvasNodeTypes, type CanvasFlowNode } from "./CanvasNodes";
import { useCanvasGraph } from "./use-canvas-graph";

const NEW_NODE_JITTER_PX = 24;

const DIMMED_OPACITY = 0.28;

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
    const derived = node.derived === true;
    const group = node.type === "episode";
    const flowNode: CanvasFlowNode = {
      id: node.id,
      type: node.type,
      position: node.position,
      data: node.data,
      selected: existing?.selected ?? false,
      ...(node.parentId ? { parentId: node.parentId } : {}),
      ...(group ? { style: { width: node.width, height: node.height }, zIndex: -1 } : {}),
      // 项目推导的节点只读：不能删，也不能单独拖离集分组（拖动整个分组）。
      draggable: !derived || group,
      deletable: !derived,
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
      deletable: !e.virtual,
      animated,
      ...(e.virtual ? { style: VIRTUAL_EDGE_STYLE } : {}),
    };
    return edge;
  });
  const unchanged = next.length === previous.length && next.every((e, i) => e === previous[i]);
  return unchanged ? (previous as Edge[]) : next;
}

const VIRTUAL_EDGE_STYLE = { strokeDasharray: "4 4", opacity: 0.6 };

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
  const signatures = useRef(new Map<string, string>());
  const graphRef = useRef(graph);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [branch, setBranch] = useState<{ steps: CanvasBranchStep[]; current: number | null } | null>(null);
  const [imageModels, setImageModels] = useState<ImageModelChoices>({ options: [], providerNames: {}, modelNames: {} });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  // 已入队、正在跟踪的任务 → 它所属的节点键（H3 写作节点 id / `集:联合图`）。
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const trackedRows = useTaskRowsByIds(Object.keys(tracked));

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
      setModel: (nodeId, model) => guarded(() => API.updateCanvasNode(projectName, nodeId, { data: { model: model || null } })),
      imageModels,
      run: async (nodeId) => {
        // 上游还有没产出或已过期的节点时，先让用户确认整条链再按顺序跑。
        try {
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
      runWriter: (nodeId) =>
        guarded(async () => {
          const byEpisode = new Map<number, string[]>();
          for (const edge of graphRef.current?.edges ?? []) {
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
    }),
    [guarded, imageModels, projectName, pushToast, t],
  );

  const executeBranch = useCallback(async () => {
    if (!branch) return;
    try {
      await runBranch(branch.steps, {
        onStep: (_step, index) => setBranch((prev) => (prev ? { ...prev, current: index } : prev)),
        getTask: (taskId) => API.getTask(taskId),
        runStep: async (step) => {
          if (step.type === "keyframe") {
            const [, , episode, sheetId] = step.id.split(":");
            return (await enqueueKeyframeSheet(projectName, Number(episode), sheetId)).taskIds;
          }
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

  const tidy = useCallback(() => {
    if (!graph) return;
    const positions = tidyLayout(graph.nodes, graph.edges.filter((e) => !e.virtual));
    if (Object.keys(positions).length === 0) return;
    void guarded(() => API.updateCanvasLayout(projectName, positions));
  }, [graph, guarded, projectName]);

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
          onlyRenderVisibleElements
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
          <button type="button" className={`pointer-events-auto ${GHOST_BTN_CLS}`} onClick={tidy}>
            <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
            {t("node_canvas_tidy")}
          </button>
          <div className="pointer-events-auto relative">
            <button type="button" className={GHOST_BTN_CLS} aria-expanded={templatesOpen} onClick={() => setTemplatesOpen((open) => !open)}>
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
