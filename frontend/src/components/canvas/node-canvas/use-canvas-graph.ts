import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/api";
import type { CanvasGraph } from "@/types/canvas";
import { graphHasBusyNode } from "./canvas-rules";

/** 推送连着时只做兜底；推送不可用（未连上 / 断线）时退回这个间隔轮询。 */
const POLL_INTERVAL_MS = 2000;
const PUSH_SAFETY_POLL_MS = 20_000;

/**
 * 画布图的服务端镜像：首次加载后订阅服务端推送，worker / Agent 写盘即刻重新读取；推送不可用时，存在排队 / 运行中节点
 * （或 Agent 面板开着）退回轮询。写操作之后由调用方 `reload`。
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
    // 项目推导的节点（资产图等）不改画布 revision，所以按内容比较：轮询到完全相同的画布时
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

  const [pushing, setPushing] = useState(false);
  useEffect(() => {
    let inflight = false;
    let again = false;
    // 推送事件可能连发（一次运行会写好几次盘）：同一时刻只有一次读取在途，期间到达的合并成再读一次。
    const refresh = async () => {
      if (inflight) {
        again = true;
        return;
      }
      inflight = true;
      try {
        do {
          again = false;
          await reload();
        } while (again);
      } finally {
        inflight = false;
      }
    };
    const handle = API.openCanvasStream({
      projectName,
      onOpen: () => {
        setPushing(true);
        void refresh();
      },
      onChanged: () => void refresh(),
      onError: () => setPushing(false),
    });
    return () => {
      handle.close();
      setPushing(false);
    };
  }, [projectName, reload]);

  // 推送连着时低频兜底；推送不可用时，Agent 面板开着或有节点在跑才按短间隔轮询。
  const busy = (graph ? graphHasBusyNode(graph.nodes) : false) || live;
  const pollMs = pushing ? PUSH_SAFETY_POLL_MS : busy ? POLL_INTERVAL_MS : null;
  useEffect(() => {
    if (pollMs === null) return;
    // 标签页在后台时不轮询；切回来由下一次计时器补上。
    const timer = setInterval(() => {
      if (!document.hidden) void reload();
    }, pollMs);
    return () => clearInterval(timer);
  }, [pollMs, reload]);

  return { graph, error, reload };
}
