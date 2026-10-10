import { describe, expect, it } from "vitest";
import type { CanvasNode } from "@/types/canvas";
import { buildAgentNote, graphHasBusyNode, isValidCanvasConnection, lineageOf, referencedNodeIds } from "./canvas-rules";

const node = (id: string, type: CanvasNode["type"], status?: CanvasNode["data"]["status"]): CanvasNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { status },
});
const nodes = [node("t", "text"), node("i", "image"), node("g", "image_generate")];

describe("isValidCanvasConnection", () => {
  it("accepts matching handle types", () => {
    expect(isValidCanvasConnection(nodes, { source: "t", sourceHandle: "text", target: "g", targetHandle: "prompt" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { source: "i", sourceHandle: "image", target: "g", targetHandle: "image1" })).toBe(true);
  });

  it("rejects mismatched types, unknown handles and self loops", () => {
    expect(isValidCanvasConnection(nodes, { source: "t", sourceHandle: "text", target: "g", targetHandle: "image1" })).toBe(false);
    expect(isValidCanvasConnection(nodes, { source: "i", sourceHandle: "image", target: "g", targetHandle: "prompt" })).toBe(false);
    expect(isValidCanvasConnection(nodes, { source: "t", sourceHandle: "text", target: "i", targetHandle: "in" })).toBe(false);
    expect(isValidCanvasConnection(nodes, { source: "g", sourceHandle: "text", target: "g", targetHandle: "prompt" })).toBe(false);
  });
});

describe("graphHasBusyNode", () => {
  it("is true only while a node is queued or running", () => {
    expect(graphHasBusyNode([node("a", "image", "done")])).toBe(false);
    expect(graphHasBusyNode([node("a", "image", "queued")])).toBe(true);
    expect(graphHasBusyNode([node("a", "image_generate", "running")])).toBe(true);
  });
});

describe("lineageOf", () => {
  const edges = [
    { id: "1", source: "a", target: "b" },
    { id: "2", source: "b", target: "c" },
    { id: "3", source: "x", target: "c" },
    { id: "4", source: "z", target: "y" },
  ];

  it("walks ancestors and descendants but not siblings or unrelated chains", () => {
    const { nodeIds, edgeIds } = lineageOf("b", edges);
    expect([...nodeIds].sort()).toEqual(["a", "b", "c"]);
    expect([...edgeIds].sort()).toEqual(["1", "2"]);
  });

  it("terminates on cycles", () => {
    const { nodeIds } = lineageOf("a", [
      { id: "1", source: "a", target: "b" },
      { id: "2", source: "b", target: "a" },
    ]);
    expect([...nodeIds].sort()).toEqual(["a", "b"]);
  });
});

describe("video and audio providers", () => {
  const media: CanvasNode[] = [
    { id: "t", type: "text", position: { x: 0, y: 0 }, data: {} },
    { id: "i", type: "image", position: { x: 0, y: 0 }, data: {} },
    { id: "v", type: "video_generate", position: { x: 0, y: 0 }, data: {} },
    { id: "a", type: "audio_generate", position: { x: 0, y: 0 }, data: {} },
    { id: "clip", type: "video", position: { x: 0, y: 0 }, data: {} },
  ];

  it("takes text and an optional start frame for video, text only for voice", () => {
    expect(isValidCanvasConnection(media, { source: "t", sourceHandle: "text", target: "v", targetHandle: "prompt" })).toBe(true);
    expect(isValidCanvasConnection(media, { source: "i", sourceHandle: "image", target: "v", targetHandle: "frame" })).toBe(true);
    expect(isValidCanvasConnection(media, { source: "t", sourceHandle: "text", target: "a", targetHandle: "text" })).toBe(true);
    expect(isValidCanvasConnection(media, { source: "i", sourceHandle: "image", target: "a", targetHandle: "text" })).toBe(false);
  });

  it("does not let a produced clip feed back into an image slot", () => {
    expect(isValidCanvasConnection(media, { source: "clip", sourceHandle: "video", target: "v", targetHandle: "frame" })).toBe(false);
  });
});

describe("image sources inside groups", () => {
  const nodes: CanvasNode[] = [
    { id: "sheet", type: "image", position: { x: 0, y: 0 }, data: {} },
    { id: "g", type: "image_generate", position: { x: 0, y: 0 }, data: {} },
    { id: "v", type: "video_generate", position: { x: 0, y: 0 }, data: {} },
  ];

  it("offers the character sheet as image1 or a video start frame, never as text", () => {
    const sheet = { source: "sheet", sourceHandle: "image" };
    expect(isValidCanvasConnection(nodes, { ...sheet, target: "g", targetHandle: "image1" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...sheet, target: "v", targetHandle: "frame" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...sheet, target: "g", targetHandle: "prompt" })).toBe(false);
  });

  it("gives image generators a single image output and no text output", () => {
    const out = { source: "g", target: "g2" };
    const more: CanvasNode[] = [...nodes, { id: "g2", type: "image_generate", position: { x: 0, y: 0 }, data: {} }];
    expect(isValidCanvasConnection(more, { ...out, sourceHandle: "image", targetHandle: "image1" })).toBe(true);
    expect(isValidCanvasConnection(more, { ...out, sourceHandle: "text", targetHandle: "prompt" })).toBe(false);
  });
});

describe("agent context", () => {
  const nodes = [
    { id: "p1", type: "text", position: { x: 0, y: 0 }, data: { text: "hello" } },
    { id: "p2", type: "text", position: { x: 0, y: 0 }, data: { text: "bye" } },
  ] as unknown as CanvasNode[];

  it("merges chips with @mentions and drops unknown ids", () => {
    expect(referencedNodeIds("@p2 only face", ["p1", "gone"], nodes)).toEqual(["p1", "p2"]);
  });

  it("prefixes the note with referenced node content", () => {
    const note = buildAgentNote("only face", ["p1"], nodes);
    expect(note).toContain("- p1 (text)");
    expect(note).toContain("hello");
    expect(note.endsWith("only face")).toBe(true);
    expect(buildAgentNote("hi", [], nodes)).toBe("hi");
  });
});

describe("lineage of groups", () => {
  const nodes = [
    { id: "g", parentId: undefined },
    { id: "a", parentId: "g" },
    { id: "b", parentId: "g" },
    { id: "other", parentId: undefined },
  ];
  const edges = [
    { id: "e1", source: "a", target: "b" },
    { id: "e2", source: "b", target: "other" },
  ];

  it("highlights a selected group together with everything inside it", () => {
    const lineage = lineageOf("g", edges, nodes);
    expect([...lineage.nodeIds].sort()).toEqual(["a", "b", "g"]);
    expect([...lineage.edgeIds]).toEqual(["e1"]);
  });

  it("keeps the group frame bright when a child is selected", () => {
    expect(lineageOf("a", edges, nodes).nodeIds.has("g")).toBe(true);
  });
});

describe("asset port connections", () => {
  const nodes = [
    { id: "p:char_port:A", type: "asset", position: { x: 0, y: 0 }, data: {} },
    { id: "gen", type: "image_generate", position: { x: 0, y: 0 }, data: {} },
    { id: "vid", type: "video_generate", position: { x: 0, y: 0 }, data: {} },
  ] as unknown as CanvasNode[];

  it("connects an asset output to reference-image inputs only", () => {
    const from = { source: "p:char_port:A", sourceHandle: "asset" };
    expect(isValidCanvasConnection(nodes, { ...from, target: "gen", targetHandle: "image1" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...from, target: "vid", targetHandle: "frame" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...from, target: "gen", targetHandle: "prompt" })).toBe(false);
  });
});


describe("buildAgentNote for a group", () => {
  it("lists the group's documents and counts its units", () => {
    const nodes = [
      { id: "group_1", type: "group", position: { x: 0, y: 0 }, data: { episode: 1, label: "EP01" } },
      { id: "markdown_1", type: "markdown", parentId: "group_1", position: { x: 0, y: 0 }, data: { step: "source", file: "source/episode_1.txt" } },
      { id: "markdown_2", type: "markdown", parentId: "group_1", position: { x: 0, y: 0 }, data: { step: "screenplay", file: "drafts/episode_1/screenplay.json", screenplay_status: "approved" } },
      { id: "text_1", type: "text", parentId: "group_1", position: { x: 0, y: 0 }, data: { unit: "E1U01", step: "script" } },
    ] as unknown as Parameters<typeof buildAgentNote>[2];

    const note = buildAgentNote("continue", ["group_1"], nodes);

    expect(note).toContain("markdown_2 (markdown, step=screenplay");
    expect(note).toContain("screenplay_status=approved");
    expect(note).toContain("1 units on the canvas");
  });
});
