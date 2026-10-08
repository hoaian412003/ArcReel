import { describe, expect, it } from "vitest";
import type { CanvasEdge, CanvasNode } from "@/types/canvas";
import { tidyLayout } from "./canvas-layout";

const node = (id: string, x: number, y: number, derived = false): CanvasNode => ({
  id,
  type: "text",
  position: { x, y },
  data: {},
  ...(derived ? { derived: true } : {}),
});
const edge = (source: string, target: string): CanvasEdge => ({
  id: `${source}>${target}`,
  source,
  sourceHandle: "text",
  target,
  targetHandle: "prompt",
});

describe("tidyLayout", () => {
  it("puts each node one column right of its deepest upstream and stacks siblings", () => {
    const positions = tidyLayout(
      [node("a", 100, 100), node("b", 900, 120), node("c", 50, 700), node("d", 0, 0)],
      [edge("a", "c"), edge("b", "c"), edge("c", "d")],
    );
    expect(positions.a.x).toBe(positions.b.x);
    expect(positions.c.x).toBeGreaterThan(positions.a.x);
    expect(positions.d.x).toBeGreaterThan(positions.c.x);
    expect(positions.b.y).toBeGreaterThan(positions.a.y);
  });

  it("leaves project-derived nodes alone and survives cycles", () => {
    const positions = tidyLayout(
      [node("a", 0, 0), node("b", 0, 0), node("p:unit:1:U", 0, 0, true)],
      [edge("a", "b"), edge("b", "a"), edge("p:unit:1:U", "a")],
    );
    expect(Object.keys(positions).sort()).toEqual(["a", "b"]);
  });

  it("is a no-op for an empty canvas", () => {
    expect(tidyLayout([], [])).toEqual({});
  });
});
