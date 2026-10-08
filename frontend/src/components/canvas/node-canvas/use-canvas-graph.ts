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

  const apply = useCallback((next: CanvasGraph) => {
    setError(null);
    if (next.revision >= latestRevision.current) {
      latestRevision.current = next.revision;
      setGraph(next);
    }
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
    const timer = setInterval(() => void reload(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [busy, reload]);

  return { graph, error, reload };
}
