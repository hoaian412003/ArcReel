import { describe, expect, it } from "vitest";
import type { CanvasEdge, CanvasNode } from "@/types/canvas";
import { tidyEpisodeGroup, tidyLayout } from "./canvas-layout";

const node = (id: string, x: number, y: number, parentId?: string): CanvasNode => ({
  id,
  type: "text",
  position: { x, y },
  data: {},
  ...(parentId ? { parentId } : {}),
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

  it("leaves nodes inside groups alone and survives cycles", () => {
    const positions = tidyLayout(
      [node("a", 0, 0), node("b", 0, 0), node("inside", 0, 0, "group")],
      [edge("a", "b"), edge("b", "a"), edge("inside", "a")],
    );
    expect(Object.keys(positions).sort()).toEqual(["a", "b"]);
  });

  it("is a no-op for an empty canvas", () => {
    expect(tidyLayout([], [])).toEqual({});
  });
});


describe("tidyLayout with measured sizes", () => {
  it("leaves room for a node that grew tall instead of stacking on a fixed pitch", () => {
    const sizes = new Map([
      ["a", { width: 300, height: 900 }],
      ["b", { width: 300, height: 200 }],
    ]);
    const positions = tidyLayout([node("a", 0, 0), node("b", 0, 50)], [], sizes);

    expect(positions.b.y).toBeGreaterThanOrEqual(positions.a.y + 900);
  });
});

describe("tidyEpisodeGroup", () => {
  const unitNode = (id: string, unit: string, step: string, y: number): CanvasNode => ({
    id,
    type: "text",
    position: { x: 0, y },
    data: { unit, step } as CanvasNode["data"],
    parentId: "g",
  });

  it("gives each unit row the height of its tallest node and aligns columns by step", () => {
    const members = [
      unitNode("s1", "U1", "script", 400),
      unitNode("d1", "U1", "direction", 400),
      unitNode("s2", "U2", "script", 800),
      unitNode("d2", "U2", "direction", 800),
    ];
    const sizes = new Map([
      ["s1", { width: 300, height: 700 }],
      ["d1", { width: 300, height: 200 }],
      ["s2", { width: 300, height: 200 }],
      ["d2", { width: 300, height: 200 }],
    ]);

    const { positions, height } = tidyEpisodeGroup(members, sizes);

    expect(positions.s1.y).toBe(positions.d1.y);
    expect(positions.s2.y).toBeGreaterThanOrEqual(positions.s1.y + 700);
    expect(positions.d1.x).toBeGreaterThan(positions.s1.x);
    expect(positions.s1.x).toBe(positions.s2.x);
    expect(height).toBeGreaterThan(positions.s2.y + 200);
  });

  it("puts documents on top and keeps keyframe sheets beside their first unit without overlapping each other", () => {
    const doc = (id: string, step: string): CanvasNode => ({ id, type: "markdown", position: { x: 0, y: 0 }, data: { step } as CanvasNode["data"], parentId: "g" });
    const board = (id: string, step: string, sheet: string, y: number): CanvasNode => ({ id, type: "image", position: { x: 0, y }, data: { step, sheet } as CanvasNode["data"], parentId: "g" });
    const members = [
      doc("src", "source"),
      doc("plan", "plan"),
      unitNode("s1", "U1", "script", 400),
      unitNode("s2", "U2", "script", 800),
      board("g1", "kf_gen", "S1", 400),
      board("b1", "kf_sheet", "S1", 440),
      board("g2", "kf_gen", "S2", 800),
      board("b2", "kf_sheet", "S2", 840),
    ];
    const sizes = new Map(members.map((n) => [n.id, { width: 300, height: n.id === "s1" ? 150 : 400 }]));

    const { positions } = tidyEpisodeGroup(members, sizes);

    expect(positions.src.y).toBeLessThan(positions.s1.y);
    expect(positions.plan.x).toBeGreaterThan(positions.src.x);
    expect(positions.g2.y).toBeGreaterThanOrEqual(positions.b1.y + 400);
    expect(positions.g1.x).toBeLessThan(positions.s1.x);
  });
});
