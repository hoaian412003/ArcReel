import { describe, expect, it } from "vitest";
import type { CanvasNode } from "@/types/canvas";
import { graphHasBusyNode, isValidCanvasConnection, lineageOf } from "./canvas-rules";

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
    expect(isValidCanvasConnection(nodes, { source: "i", sourceHandle: "image", target: "g", targetHandle: "refs" })).toBe(true);
  });

  it("rejects mismatched types, unknown handles and self loops", () => {
    expect(isValidCanvasConnection(nodes, { source: "t", sourceHandle: "text", target: "g", targetHandle: "refs" })).toBe(false);
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

describe("derived node connections", () => {
  const derived: CanvasNode[] = [
    { id: "u", type: "unit", position: { x: 0, y: 0 }, data: {}, derived: true },
    { id: "k", type: "keyframe", position: { x: 0, y: 0 }, data: {}, derived: true },
    { id: "w", type: "h3_writer", position: { x: 0, y: 0 }, data: {} },
    { id: "g", type: "image_generate", position: { x: 0, y: 0 }, data: {} },
  ];

  it("wires units into the writer and units/keyframes into the image generator", () => {
    expect(isValidCanvasConnection(derived, { source: "u", sourceHandle: "unit", target: "w", targetHandle: "units" })).toBe(true);
    expect(isValidCanvasConnection(derived, { source: "u", sourceHandle: "text", target: "g", targetHandle: "prompt" })).toBe(true);
    expect(isValidCanvasConnection(derived, { source: "k", sourceHandle: "image", target: "g", targetHandle: "refs" })).toBe(true);
    expect(isValidCanvasConnection(derived, { source: "u", sourceHandle: "text", target: "w", targetHandle: "units" })).toBe(false);
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

describe("character nodes", () => {
  const nodes: CanvasNode[] = [
    { id: "p:character:Hero", type: "character", position: { x: 0, y: 0 }, data: {}, derived: true },
    { id: "g", type: "image_generate", position: { x: 0, y: 0 }, data: {} },
    { id: "v", type: "video_generate", position: { x: 0, y: 0 }, data: {} },
  ];

  it("offers the character sheet as a reference image or a video start frame, never as text", () => {
    const hero = { source: "p:character:Hero", sourceHandle: "image" };
    expect(isValidCanvasConnection(nodes, { ...hero, target: "g", targetHandle: "refs" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...hero, target: "v", targetHandle: "frame" })).toBe(true);
    expect(isValidCanvasConnection(nodes, { ...hero, target: "g", targetHandle: "prompt" })).toBe(false);
  });
});
