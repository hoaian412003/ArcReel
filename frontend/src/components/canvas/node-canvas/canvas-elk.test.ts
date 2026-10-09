import { describe, expect, it } from "vitest";
import { layoutGroup } from "./canvas-elk";

describe("layoutGroup", () => {
  const box = (id: string, height = 200) => ({ id, type: "text" as const, width: 240, height });

  it("lays a pipeline left to right and never lets tall nodes overlap", async () => {
    const children = [box("text", 400), box("ref", 400), { ...box("gen"), type: "image_generate" as const }, box("sheet", 300)];
    const edges = [
      { source: "text", target: "gen" },
      { source: "ref", target: "gen" },
      { source: "gen", target: "sheet" },
    ];

    const result = await layoutGroup(children, edges);

    const at = (id: string) => ({ ...result.positions[id], height: children.find((c) => c.id === id)?.height ?? 0 });
    expect(at("gen").x).toBeGreaterThan(at("text").x);
    expect(at("sheet").x).toBeGreaterThan(at("gen").x);
    // 同一层里的两个高节点上下不重叠。
    const [upper, lower] = [at("text"), at("ref")].sort((a, b) => (a.y ?? 0) - (b.y ?? 0));
    expect((upper?.y ?? 0) + (upper?.height ?? 0)).toBeLessThanOrEqual(lower?.y ?? 0);
    expect(result.height).toBeGreaterThanOrEqual(800);
  });
});
