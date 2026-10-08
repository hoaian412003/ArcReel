import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import type { CanvasGraph } from "@/types/canvas";
import { graphHasBusyNode } from "./canvas-rules";

const POLL_INTERVAL_MS = 2000;

/**
 * 画布图的服务端镜像：首次加载，存在排队 / 运行中节点（或 Agent 面板开着）时轮询，写操作之后由调用方 `reload`。
 * 服务端 revision 只增不减，较旧的响应（慢请求晚到）直接丢弃。
 */
export function useCanvasGraph(projectName: string, live = false) {
  const [graph, setGraph] = useState<CanvasGraph | null>(null);
  const [error, setError] = useState<unknown>(null);
  const latestRevision = useRef(-1);

  const lastSnapshot = useRef("");

  const apply = useCallback((next: CanvasGraph) => {
    setError(null);
    if (next.revision < latestRevision.current) return;
    latestRevision.current = next.revision;
    // 项目推导的节点（H3 状态、联合图）不改画布 revision，所以按内容比较：轮询到完全相同的画布时
    // 保持原对象，下游不重算、不重绘。
    const snapshot = JSON.stringify(next);
    if (snapshot === lastSnapshot.current) return;
    lastSnapshot.current = snapshot;
    setGraph(next);
  }, []);

  const reload = useCallback(async () => {
    try {
      apply(await API.getCanvas(projectName));
    } catch (err) {
      setError(err);
    }
  }, [apply, projectName]);

  useEffect(() => {
    latestRevision.current = -1;
    lastSnapshot.current = "";
    const controller = new AbortController();
    API.getCanvas(projectName, { signal: controller.signal })
      .then((next) => {
        if (!controller.signal.aborted) apply(next);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err);
      });
    return () => controller.abort();
  }, [apply, projectName]);

  // live：Agent 面板开着时，Agent 随时会改画布，保持轮询让改动即时出现。
  const busy = (graph ? graphHasBusyNode(graph.nodes) : false) || live;
  useEffect(() => {
    if (!busy) return;
    // 标签页在后台时不轮询；切回来由下一次计时器补上。
    const timer = setInterval(() => {
      if (!document.hidden) void reload();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [busy, reload]);

  return { graph, error, reload };
}
