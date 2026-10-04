import { useCallback, useEffect, useRef, useState } from "react";

export const QUEUE_POLL_INTERVAL_MS = 5000;

interface PollingState<T> {
  data: T | null;
  /** 首次加载进行中（尚无数据）。 */
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * 页面可见时按固定间隔轮询 `fetcher`；标签页隐藏时暂停，重新可见时立即补一次。
 * `deps` 变化会立即重新请求（沿用上一份数据直至新数据到达）。失败保留上一份数据并暴露 error，下一轮自动重试。
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  intervalMs: number = QUEUE_POLL_INTERVAL_MS,
): PollingState<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const fetcherRef = useRef(fetcher);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;

    const run = async () => {
      controller?.abort();
      controller = new AbortController();
      const { signal } = controller;
      try {
        const data = await fetcherRef.current(signal);
        if (cancelled || signal.aborted) return;
        setState({ data, error: null, loading: false });
      } catch (err) {
        if (cancelled || signal.aborted) return;
        setState((prev) => ({
          data: prev.data,
          error: err instanceof Error ? err.message : String(err),
          loading: false,
        }));
      }
      schedule();
    };

    const schedule = () => {
      if (cancelled) return;
      clearTimeout(timer);
      if (document.visibilityState === "hidden") return;
      timer = setTimeout(() => void run(), intervalMs);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        clearTimeout(timer);
        void run();
      } else {
        clearTimeout(timer);
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void run();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, nonce]);

  return { data: state.data, loading: state.loading, error: state.error, refresh };
}
