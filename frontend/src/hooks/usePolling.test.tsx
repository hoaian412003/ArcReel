import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches immediately and then every interval", async () => {
    const fetcher = vi.fn().mockResolvedValue("v");
    const { result } = renderHook(() => usePolling(fetcher, [], 5000));
    await act(async () => {});
    expect(result.current.data).toBe("v");
    expect(result.current.loading).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("pauses while hidden and refetches right away when visible again", async () => {
    const fetcher = vi.fn().mockResolvedValue("v");
    renderHook(() => usePolling(fetcher, [], 5000));
    await act(async () => {});

    act(() => setVisibility("hidden"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => setVisibility("visible"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("keeps the previous data and exposes the error when a poll fails, then recovers", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("ok").mockRejectedValueOnce(new Error("boom")).mockResolvedValue("back");
    const { result } = renderHook(() => usePolling(fetcher, [], 5000));
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.data).toBe("ok");
    expect(result.current.error).toBe("boom");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.data).toBe("back");
    expect(result.current.error).toBeNull();
  });

  it("refetches when deps change", async () => {
    const fetcher = vi.fn().mockImplementation((_signal: AbortSignal) => Promise.resolve("v"));
    const { rerender } = renderHook(({ dep }) => usePolling(fetcher, [dep], 5000), { initialProps: { dep: 1 } });
    await act(async () => {});
    rerender({ dep: 2 });
    await act(async () => {});
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
