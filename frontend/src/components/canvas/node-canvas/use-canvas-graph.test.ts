import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { CanvasGraph } from "@/types/canvas";
import { useCanvasGraph } from "./use-canvas-graph";

const graph = (revision: number, text = "a"): CanvasGraph => ({
  revision,
  nodes: [{ id: "n", type: "text", position: { x: 0, y: 0 }, data: { text } }],
  edges: [],
  view: { episodes: [] },
  episodes: [],
  templates: [],
});

afterEach(() => vi.restoreAllMocks());

describe("useCanvasGraph", () => {
  it("keeps the same graph object when a reload returns identical content", async () => {
    const spy = vi.spyOn(API, "getCanvas").mockImplementation(() => Promise.resolve(graph(3)));
    const { result } = renderHook(() => useCanvasGraph("demo"));
    await waitFor(() => expect(result.current.graph).not.toBeNull());
    const first = result.current.graph;

    await act(async () => {
      await result.current.reload();
    });

    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.graph).toBe(first);
  });

  it("takes changed project-derived content even when the revision did not move", async () => {
    const spy = vi.spyOn(API, "getCanvas").mockResolvedValueOnce(graph(3, "a")).mockResolvedValueOnce(graph(3, "b"));
    const { result } = renderHook(() => useCanvasGraph("demo"));
    await waitFor(() => expect(result.current.graph?.nodes[0].data.text).toBe("a"));

    await act(async () => {
      await result.current.reload();
    });

    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.graph?.nodes[0].data.text).toBe("b");
  });

  it("drops a slow response older than what it already has", async () => {
    vi.spyOn(API, "getCanvas").mockResolvedValueOnce(graph(5, "new")).mockResolvedValueOnce(graph(4, "old"));
    const { result } = renderHook(() => useCanvasGraph("demo"));
    await waitFor(() => expect(result.current.graph?.revision).toBe(5));

    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.graph?.nodes[0].data.text).toBe("new");
  });
});
